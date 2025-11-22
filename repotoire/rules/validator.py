"""Cypher pattern validation for custom rules (REPO-125)."""

from typing import Optional, List
from repotoire.graph.client import Neo4jClient
from repotoire.logging_config import get_logger

logger = get_logger(__name__)


class RuleValidator:
    """Validates Cypher query patterns for custom rules.

    Ensures that rule patterns are syntactically valid and safe to execute.
    Prevents common issues like:
    - Syntax errors in Cypher
    - Missing RETURN clauses
    - Dangerous operations (DELETE, DROP, CREATE without MERGE)
    - Unbounded queries that could harm performance

    Example:
        >>> validator = RuleValidator(client)
        >>> is_valid, error = validator.validate_pattern(
        ...     "MATCH (c:Class) WHERE c.complexity > 20 RETURN c"
        ... )
        >>> if not is_valid:
        ...     print(f"Invalid pattern: {error}")
    """

    # Dangerous keywords that should not appear in rule patterns
    DANGEROUS_KEYWORDS = [
        "DELETE",
        "DETACH DELETE",
        "DROP",
        "REMOVE",
        "SET",  # Allow SET but warn
    ]

    # Required components in valid patterns
    REQUIRED_KEYWORDS = [
        "MATCH",
        "RETURN",
    ]

    def __init__(self, client: Optional[Neo4jClient] = None):
        """Initialize validator.

        Args:
            client: Optional Neo4j client for syntax validation
        """
        self.client = client

    def validate_pattern(self, pattern: str) -> tuple[bool, Optional[str]]:
        """Validate a Cypher query pattern.

        Performs multiple validation checks:
        1. Pattern is not empty
        2. Contains required keywords (MATCH, RETURN)
        3. Does not contain dangerous operations
        4. Syntax is valid (if client provided)

        Args:
            pattern: Cypher query pattern to validate

        Returns:
            (is_valid, error_message) tuple
        """
        # Check 1: Not empty
        if not pattern or not pattern.strip():
            return False, "Pattern cannot be empty"

        pattern_upper = pattern.upper()

        # Check 2: Required keywords
        for keyword in self.REQUIRED_KEYWORDS:
            if keyword not in pattern_upper:
                return False, f"Pattern must contain {keyword} clause"

        # Check 3: Dangerous operations
        for keyword in self.DANGEROUS_KEYWORDS:
            if keyword in pattern_upper:
                # Allow SET in certain contexts (for updating properties)
                if keyword == "SET":
                    logger.warning(
                        f"Pattern contains SET clause - ensure it's intentional: {pattern[:50]}..."
                    )
                    continue
                return False, f"Pattern contains dangerous operation: {keyword}"

        # Check 4: Syntax validation (if client available)
        if self.client:
            is_valid, syntax_error = self._validate_syntax(pattern)
            if not is_valid:
                return False, f"Syntax error: {syntax_error}"

        # Check 5: Performance warnings
        warnings = self._check_performance_issues(pattern)
        if warnings:
            for warning in warnings:
                logger.warning(f"Performance warning: {warning}")

        return True, None

    def _validate_syntax(self, pattern: str) -> tuple[bool, Optional[str]]:
        """Validate Cypher syntax using EXPLAIN.

        Args:
            pattern: Cypher query to validate

        Returns:
            (is_valid, error_message) tuple
        """
        try:
            # Use EXPLAIN to check syntax without executing
            explain_query = f"EXPLAIN {pattern}"
            self.client.execute_query(explain_query)
            return True, None

        except Exception as e:
            error_msg = str(e)
            # Extract the useful part of the error message
            if "SyntaxError" in error_msg or "Invalid" in error_msg:
                return False, error_msg
            return False, f"Validation failed: {error_msg}"

    def _check_performance_issues(self, pattern: str) -> List[str]:
        """Check for potential performance issues.

        Args:
            pattern: Cypher query pattern

        Returns:
            List of warning messages
        """
        warnings = []

        pattern_upper = pattern.upper()

        # Warn about missing WHERE clauses
        if "WHERE" not in pattern_upper:
            warnings.append("Pattern has no WHERE clause - may scan entire graph")

        # Warn about unbounded variable-length paths
        if "*" in pattern and not any(x in pattern for x in ["*1..", "*2..", "*..10", "*..20"]):
            warnings.append("Pattern contains unbounded variable-length path (*) - may be slow")

        # Warn about missing LIMIT
        if "LIMIT" not in pattern_upper:
            warnings.append("Pattern has no LIMIT clause - may return many results")

        # Warn about Cartesian products
        match_count = pattern_upper.count("MATCH")
        if match_count > 1 and "WITH" not in pattern_upper:
            warnings.append(
                f"Pattern has {match_count} MATCH clauses without WITH - possible Cartesian product"
            )

        return warnings

    def validate_required_fields(self, pattern: str) -> tuple[bool, Optional[str]]:
        """Validate that pattern returns required fields for findings.

        Findings need at least one of:
        - file_path or filePath
        - class_name, function_name, or module_name

        Args:
            pattern: Cypher query pattern

        Returns:
            (is_valid, error_message) tuple
        """
        pattern_lower = pattern.lower()
        return_clause = self._extract_return_clause(pattern)

        if not return_clause:
            return False, "Could not find RETURN clause"

        # Check for file reference
        has_file_ref = any(
            field in return_clause
            for field in ["file_path", "filepath", "f.filepath", "file.filepath"]
        )

        # Check for entity reference
        has_entity_ref = any(
            field in return_clause
            for field in [
                "class_name", "function_name", "module_name",
                "c.name", "f.name", "m.name",
                "c.qualifiedname", "f.qualifiedname"
            ]
        )

        if not has_file_ref and not has_entity_ref:
            return False, (
                "Pattern must return at least one of: "
                "file_path, class_name, function_name, or module_name"
            )

        return True, None

    def _extract_return_clause(self, pattern: str) -> str:
        """Extract the RETURN clause from a query.

        Args:
            pattern: Cypher query

        Returns:
            RETURN clause contents (lowercase)
        """
        pattern_lower = pattern.lower()
        return_index = pattern_lower.find("return")

        if return_index == -1:
            return ""

        # Get everything after RETURN
        return_clause = pattern_lower[return_index + 6:].strip()

        # Remove trailing semicolon if present
        if ";" in return_clause:
            return_clause = return_clause[:return_clause.find(";")]

        return return_clause

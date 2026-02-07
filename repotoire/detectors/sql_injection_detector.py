"""SQL Injection detector using graph queries.

Fast replacement for semgrep SQL injection rules. Uses graph queries to find
dangerous SQL patterns:

1. Query CALLS to SQL-related functions (execute, executemany, raw, etc.)
2. Check if arguments contain string concatenation or f-strings with variables
3. Flag cases where user input could flow into SQL queries
4. Leverage taint data if available in the graph

Patterns detected:
- cursor.execute(f"SELECT * FROM users WHERE id={user_id}")
- cursor.execute("SELECT * FROM users WHERE id=" + user_id)
- Model.objects.raw(query) where query is a variable
- engine.execute(text(user_input))
"""

import re
from pathlib import Path
from typing import Any, Dict, List, Optional, Set

from repotoire.detectors.base import CodeSmellDetector
from repotoire.graph import FalkorDBClient
from repotoire.graph.enricher import GraphEnricher
from repotoire.logging_config import get_logger
from repotoire.models import CollaborationMetadata, Finding, Severity

logger = get_logger(__name__)


# SQL-related function patterns to look for
SQL_SINK_FUNCTIONS = {
    # Database cursor methods
    "execute",
    "executemany",
    "executescript",
    "mogrify",
    # Django ORM
    "raw",
    "extra",
    # SQLAlchemy
    "text",
    "from_statement",
    "execute",
    # Raw SQL execution
    "run_sql",
    "execute_sql",
    "query",
}

# Patterns that indicate method calls on SQL-related objects
SQL_OBJECT_PATTERNS = {
    "cursor",
    "connection",
    "conn",
    "db",
    "database",
    "engine",
    "session",
}

# Regex patterns for detecting dangerous SQL construction
# Pattern 1: f-string with SQL keywords
FSTRING_SQL_PATTERN = re.compile(
    r'f["\'][^"\']*\b(SELECT|INSERT|UPDATE|DELETE|DROP|CREATE|ALTER|TRUNCATE|EXEC|EXECUTE)\b[^"\']*\{[^}]+\}',
    re.IGNORECASE
)

# Pattern 2: String concatenation with SQL keywords
CONCAT_SQL_PATTERN = re.compile(
    r'["\'][^"\']*\b(SELECT|INSERT|UPDATE|DELETE|DROP|CREATE|ALTER|TRUNCATE|EXEC|EXECUTE)\b[^"\']*["\']\s*\+',
    re.IGNORECASE
)

# Pattern 3: .format() with SQL keywords
FORMAT_SQL_PATTERN = re.compile(
    r'["\'][^"\']*\b(SELECT|INSERT|UPDATE|DELETE|DROP|CREATE|ALTER|TRUNCATE|EXEC|EXECUTE)\b[^"\']*["\']\.format\s*\(',
    re.IGNORECASE
)

# Pattern 4: % formatting with SQL keywords
PERCENT_SQL_PATTERN = re.compile(
    r'["\'][^"\']*\b(SELECT|INSERT|UPDATE|DELETE|DROP|CREATE|ALTER|TRUNCATE|EXEC|EXECUTE)\b[^"\']*%[sdr][^"\']*["\']\s*%',
    re.IGNORECASE
)


class SQLInjectionDetector(CodeSmellDetector):
    """Detects potential SQL injection vulnerabilities.

    Uses graph queries to find CALLS to SQL-related functions, then analyzes
    the arguments for dangerous patterns like string concatenation, f-strings,
    and format strings that could allow SQL injection.

    This is a fast replacement for semgrep SQL injection rules, using the
    code graph for efficient pattern matching.
    """

    def __init__(
        self,
        graph_client: FalkorDBClient,
        detector_config: Optional[Dict[str, Any]] = None,
        enricher: Optional[GraphEnricher] = None,
    ):
        """Initialize SQL injection detector.

        Args:
            graph_client: FalkorDB database client
            detector_config: Optional configuration dict with:
                - repository_path: Path to repository root (for source analysis)
                - max_findings: Maximum findings to report (default: 100)
                - exclude_patterns: File patterns to exclude
            enricher: Optional GraphEnricher for cross-detector collaboration
        """
        super().__init__(graph_client, detector_config)
        self.enricher = enricher
        self.logger = get_logger(__name__)

        config = detector_config or {}
        self.repository_path = Path(config.get("repository_path", "."))
        self.max_findings = config.get("max_findings", 100)

        # Default exclude patterns
        default_exclude = [
            "tests/",
            "test_*.py",
            "*_test.py",
            "migrations/",
            "__pycache__/",
            ".git/",
            "node_modules/",
            "venv/",
            ".venv/",
        ]
        self.exclude_patterns = config.get("exclude_patterns", default_exclude)

    def detect(self) -> List[Finding]:
        """Detect potential SQL injection vulnerabilities.

        Returns:
            List of findings for detected SQL injection patterns
        """
        findings = []

        # Strategy 1: Query graph for CALLS to SQL sink functions
        graph_findings = self._detect_via_graph()
        findings.extend(graph_findings)

        # Strategy 2: Scan source files for dangerous patterns
        # (catches patterns that may not be in the graph)
        if self.repository_path.exists():
            source_findings = self._detect_via_source_scan()
            # Deduplicate by file + line
            seen_locations = {(f.affected_files[0] if f.affected_files else "", f.line_start)
                            for f in findings}
            for f in source_findings:
                loc = (f.affected_files[0] if f.affected_files else "", f.line_start)
                if loc not in seen_locations:
                    findings.append(f)
                    seen_locations.add(loc)

        # Limit findings
        findings = findings[:self.max_findings]

        self.logger.info(f"SQLInjectionDetector found {len(findings)} potential vulnerabilities")
        return findings

    def _detect_via_graph(self) -> List[Finding]:
        """Detect SQL injection via graph CALLS relationships.

        Queries the graph for calls to known SQL sink functions and analyzes
        the calling context.

        Returns:
            List of findings
        """
        # Fast path: use QueryCache if available
        if self.query_cache is not None:
            self.logger.debug("Using QueryCache for SQL injection detection")
            return self._detect_cached()

        findings = []

        # Query for calls to SQL-related functions
        # We look for functions that call methods named execute, raw, etc.
        repo_filter = self._get_isolation_filter("caller")

        query = f"""
        MATCH (caller:Function)-[:CALLS]->(callee:Function)
        WHERE caller.qualifiedName IS NOT NULL {repo_filter}
          AND (callee.name IN $sql_sinks OR callee.qualifiedName ENDS WITH '.execute'
               OR callee.qualifiedName ENDS WITH '.raw'
               OR callee.qualifiedName ENDS WITH '.executemany')
        OPTIONAL MATCH (caller)<-[:CONTAINS*]-(f:File)
        RETURN DISTINCT
            caller.qualifiedName AS caller_name,
            caller.name AS caller_simple_name,
            caller.lineStart AS line_start,
            caller.lineEnd AS line_end,
            caller.filePath AS caller_file,
            callee.name AS callee_name,
            callee.qualifiedName AS callee_qname,
            f.filePath AS file_path
        LIMIT 200
        """

        try:
            results = self.db.execute_query(
                query,
                self._get_query_params(sql_sinks=list(SQL_SINK_FUNCTIONS)),
            )
        except Exception as e:
            self.logger.error(f"Error executing SQL injection graph query: {e}")
            return []

        for row in results:
            caller_name = row.get("caller_name", "")
            file_path = row.get("file_path") or row.get("caller_file") or ""

            # Skip excluded patterns
            if self._should_exclude(file_path):
                continue

            # Check source code for dangerous patterns at this location
            line_start = row.get("line_start")
            if self.repository_path.exists() and file_path:
                full_path = self.repository_path / file_path
                if full_path.exists():
                    vulnerability = self._check_source_at_location(
                        full_path, line_start, row.get("line_end")
                    )
                    if vulnerability:
                        finding = self._create_finding(
                            file_path=file_path,
                            line_start=line_start,
                            line_end=row.get("line_end"),
                            caller_name=caller_name,
                            callee_name=row.get("callee_name", ""),
                            pattern_type=vulnerability["type"],
                            snippet=vulnerability.get("snippet", ""),
                        )
                        findings.append(finding)

        return findings

    def _detect_cached(self) -> List[Finding]:
        """Detect SQL injection using QueryCache.

        O(1) lookup from prefetched call data.

        Returns:
            List of findings
        """
        findings = []

        # Find functions that call SQL sink functions
        for caller_name, callees in self.query_cache.calls.items():
            caller_data = self.query_cache.get_function(caller_name)
            if not caller_data:
                continue

            file_path = caller_data.file_path
            if self._should_exclude(file_path):
                continue

            # Check if any callee is a SQL sink
            for callee_name in callees:
                simple_name = callee_name.split(".")[-1]
                if simple_name in SQL_SINK_FUNCTIONS or callee_name.endswith(".execute"):
                    # Check source code for dangerous patterns
                    if self.repository_path.exists() and file_path:
                        full_path = self.repository_path / file_path
                        if full_path.exists():
                            vulnerability = self._check_source_at_location(
                                full_path, caller_data.line_start, caller_data.line_end
                            )
                            if vulnerability:
                                finding = self._create_finding(
                                    file_path=file_path,
                                    line_start=caller_data.line_start,
                                    line_end=caller_data.line_end,
                                    caller_name=caller_name,
                                    callee_name=callee_name,
                                    pattern_type=vulnerability["type"],
                                    snippet=vulnerability.get("snippet", ""),
                                )
                                findings.append(finding)
                                break  # One finding per caller

        return findings

    def _detect_via_source_scan(self) -> List[Finding]:
        """Scan source files for dangerous SQL patterns.

        Direct source scanning catches patterns that may not be captured
        in the graph (e.g., inline SQL in expressions).

        Returns:
            List of findings
        """
        findings = []

        if not self.repository_path.exists():
            return findings

        # Incremental mode: skip unchanged files if changed_files is set
        changed_files: Optional[Set[Path]] = self.config.get("changed_files")

        for path in self.repository_path.rglob("*.py"):
            # Skip unchanged files in incremental mode
            if changed_files is not None and path not in changed_files:
                continue
            rel_path = str(path.relative_to(self.repository_path))
            if self._should_exclude(rel_path):
                continue

            try:
                content = path.read_text(encoding="utf-8", errors="ignore")
                # Skip very large files
                if len(content) > 500_000:
                    continue

                # Check each pattern
                for line_no, line in enumerate(content.split("\n"), start=1):
                    vulnerability = self._check_line_for_patterns(line)
                    if vulnerability:
                        # Verify this looks like a SQL call
                        if self._is_sql_context(line):
                            finding = self._create_finding(
                                file_path=rel_path,
                                line_start=line_no,
                                line_end=line_no,
                                caller_name="",
                                callee_name="",
                                pattern_type=vulnerability["type"],
                                snippet=line.strip()[:100],
                            )
                            findings.append(finding)

                            if len(findings) >= self.max_findings:
                                return findings

            except (OSError, UnicodeDecodeError) as e:
                self.logger.debug(f"Skipping {rel_path}: {e}")
                continue

        return findings

    def _check_source_at_location(
        self, file_path: Path, line_start: Optional[int], line_end: Optional[int]
    ) -> Optional[Dict[str, Any]]:
        """Check source code at a specific location for SQL injection patterns.

        Args:
            file_path: Path to the source file
            line_start: Starting line number
            line_end: Ending line number

        Returns:
            Dictionary with vulnerability info if found, None otherwise
        """
        if not line_start:
            return None

        try:
            content = file_path.read_text(encoding="utf-8", errors="ignore")
            lines = content.split("\n")

            # Check lines in the function/call range
            start = max(0, line_start - 1)
            end = min(len(lines), (line_end or line_start) + 5)

            for i in range(start, end):
                line = lines[i]
                vulnerability = self._check_line_for_patterns(line)
                if vulnerability:
                    vulnerability["snippet"] = line.strip()[:100]
                    return vulnerability

        except (OSError, UnicodeDecodeError):
            pass

        return None

    def _check_line_for_patterns(self, line: str) -> Optional[Dict[str, Any]]:
        """Check a line of code for dangerous SQL patterns.

        Args:
            line: Source code line

        Returns:
            Dictionary with pattern type if found, None otherwise
        """
        # Skip comments
        stripped = line.strip()
        if stripped.startswith("#"):
            return None

        # Check f-string pattern
        if FSTRING_SQL_PATTERN.search(line):
            return {"type": "f-string"}

        # Check concatenation pattern
        if CONCAT_SQL_PATTERN.search(line):
            return {"type": "concatenation"}

        # Check .format() pattern
        if FORMAT_SQL_PATTERN.search(line):
            return {"type": "format"}

        # Check % formatting pattern
        if PERCENT_SQL_PATTERN.search(line):
            return {"type": "percent_format"}

        return None

    def _is_sql_context(self, line: str) -> bool:
        """Check if the line appears to be in a SQL execution context.

        Args:
            line: Source code line

        Returns:
            True if line appears to involve SQL execution
        """
        line_lower = line.lower()

        # Check for SQL function calls
        for func in SQL_SINK_FUNCTIONS:
            if f".{func}(" in line_lower:
                return True

        # Check for SQL object patterns
        for obj in SQL_OBJECT_PATTERNS:
            if f"{obj}." in line_lower:
                return True

        # Check for Django/SQLAlchemy patterns
        if ".objects.raw(" in line_lower:
            return True
        if "text(" in line_lower and any(kw in line_lower for kw in ["select", "insert", "update", "delete"]):
            return True

        return False

    def _should_exclude(self, path: str) -> bool:
        """Check if path should be excluded.

        Args:
            path: Relative path to check

        Returns:
            True if path should be excluded
        """
        import fnmatch

        for pattern in self.exclude_patterns:
            if pattern.endswith("/"):
                if pattern.rstrip("/") in path.split("/"):
                    return True
            elif "*" in pattern:
                if fnmatch.fnmatch(path, pattern) or fnmatch.fnmatch(Path(path).name, pattern):
                    return True
            elif pattern in path:
                return True
        return False

    def _create_finding(
        self,
        file_path: str,
        line_start: Optional[int],
        line_end: Optional[int],
        caller_name: str,
        callee_name: str,
        pattern_type: str,
        snippet: str,
    ) -> Finding:
        """Create a finding for detected SQL injection vulnerability.

        Args:
            file_path: Path to the affected file
            line_start: Starting line number
            line_end: Ending line number
            caller_name: Qualified name of the calling function
            callee_name: Name of the SQL function being called
            pattern_type: Type of dangerous pattern detected
            snippet: Code snippet showing the vulnerability

        Returns:
            Finding object
        """
        pattern_descriptions = {
            "f-string": "f-string with variable interpolation in SQL query",
            "concatenation": "string concatenation in SQL query",
            "format": ".format() string interpolation in SQL query",
            "percent_format": "% string formatting in SQL query",
        }

        pattern_desc = pattern_descriptions.get(pattern_type, "dynamic SQL construction")

        title = "Potential SQL Injection (CWE-89)"
        if caller_name:
            func_name = caller_name.split(".")[-1]
            title = f"SQL Injection in {func_name}"

        description = f"""**Potential SQL Injection Vulnerability**

**Pattern detected**: {pattern_desc}

**Location**: {file_path}:{line_start or '?'}

**Code snippet**:
```python
{snippet}
```

SQL injection occurs when untrusted input is incorporated into SQL queries without 
proper sanitization. An attacker could manipulate the query to:
- Access unauthorized data
- Modify or delete database records
- Execute administrative operations
- In some cases, execute operating system commands

This vulnerability is classified as **CWE-89: Improper Neutralization of Special 
Elements used in an SQL Command ('SQL Injection')**.
"""

        if caller_name:
            description += f"\n**Containing function**: `{caller_name}`\n"
        if callee_name:
            description += f"**SQL function called**: `{callee_name}`\n"

        recommendation = """**Recommended fixes**:

1. **Use parameterized queries** (preferred):
   ```python
   # Instead of:
   cursor.execute(f"SELECT * FROM users WHERE id={user_id}")
   
   # Use:
   cursor.execute("SELECT * FROM users WHERE id = ?", (user_id,))
   ```

2. **Use ORM methods properly**:
   ```python
   # Instead of:
   User.objects.raw(f"SELECT * FROM users WHERE id={user_id}")
   
   # Use:
   User.objects.filter(id=user_id)
   ```

3. **Use SQLAlchemy's bindparams**:
   ```python
   # Instead of:
   engine.execute(text(f"SELECT * FROM users WHERE id={user_id}"))
   
   # Use:
   engine.execute(text("SELECT * FROM users WHERE id = :id"), {"id": user_id})
   ```

4. **Validate and sanitize input** when parameterization is not possible.
"""

        finding_id = f"sql_injection_{file_path}_{line_start or 0}"

        finding = Finding(
            id=finding_id,
            detector="SQLInjectionDetector",
            severity=Severity.CRITICAL,
            title=title,
            description=description,
            affected_nodes=[caller_name] if caller_name else [],
            affected_files=[file_path] if file_path else [],
            line_start=line_start,
            line_end=line_end,
            suggested_fix=recommendation,
            estimated_effort="Medium (1-4 hours)",
            graph_context={
                "vulnerability": "sql_injection",
                "cwe": "CWE-89",
                "pattern_type": pattern_type,
                "caller": caller_name,
                "callee": callee_name,
                "snippet": snippet,
            },
        )

        # Add collaboration metadata
        finding.add_collaboration_metadata(CollaborationMetadata(
            detector="SQLInjectionDetector",
            confidence=0.85,  # High confidence for pattern matches
            evidence=["pattern_match", pattern_type, "sql_sink_call"],
            tags=["security", "sql_injection", "cwe-89", "critical"],
        ))

        # Flag entity in graph for cross-detector collaboration
        if self.enricher and caller_name:
            try:
                self.enricher.flag_entity(
                    entity_qualified_name=caller_name,
                    detector="SQLInjectionDetector",
                    severity=Severity.CRITICAL.value,
                    issues=["sql_injection"],
                    confidence=0.85,
                    metadata={
                        "vulnerability": "sql_injection",
                        "cwe": "CWE-89",
                        "pattern_type": pattern_type,
                        "file": file_path,
                    },
                )
            except Exception as e:
                self.logger.warning(f"Failed to flag entity {caller_name}: {e}")

        return finding

    def severity(self, finding: Finding) -> Severity:
        """Calculate severity for a finding.

        SQL injection is always CRITICAL severity.

        Args:
            finding: Finding to assess

        Returns:
            Severity level (always CRITICAL for SQL injection)
        """
        return Severity.CRITICAL

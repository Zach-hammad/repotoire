"""Async Anti-Pattern detector - identifies async-specific code smells (REPO-228).

Detects common async anti-patterns that cause performance issues:
1. Blocking calls in async functions (time.sleep, requests, subprocess)
2. Wasteful async - async functions with no await calls
3. Sync I/O in async context (open(), input())

Leverages the function_async_yield_idx index: (f.is_async, f.has_yield)
"""

import uuid
from datetime import datetime
from typing import Dict, List, Optional, Set

from repotoire.detectors.base import CodeSmellDetector
from repotoire.graph.base import DatabaseClient
from repotoire.graph.enricher import GraphEnricher
from repotoire.models import CollaborationMetadata, Finding, Severity
from repotoire.logging_config import get_logger

logger = get_logger(__name__)


class AsyncAntipatternDetector(CodeSmellDetector):
    """Detects async-specific anti-patterns in async functions.

    Uses the function_async_yield_idx index for efficient queries on
    is_async and has_yield properties.

    Anti-patterns detected:
    - Blocking calls: time.sleep(), requests.*, subprocess.run(), etc.
    - Wasteful async: async def with no await (unnecessary overhead)
    - Sync I/O: open(), input() instead of aiofiles/async alternatives
    """

    # Blocking functions that should not be called from async code
    BLOCKING_CALLS: Dict[str, str] = {
        # Time/sleep
        "time.sleep": "asyncio.sleep",
        "sleep": "asyncio.sleep",
        # HTTP requests
        "requests.get": "aiohttp.ClientSession.get or httpx.AsyncClient.get",
        "requests.post": "aiohttp.ClientSession.post or httpx.AsyncClient.post",
        "requests.put": "aiohttp.ClientSession.put or httpx.AsyncClient.put",
        "requests.delete": "aiohttp.ClientSession.delete or httpx.AsyncClient.delete",
        "requests.patch": "aiohttp.ClientSession.patch or httpx.AsyncClient.patch",
        "requests.request": "aiohttp or httpx async client",
        "urllib.request.urlopen": "aiohttp or httpx async client",
        # Subprocess
        "subprocess.run": "asyncio.create_subprocess_exec",
        "subprocess.call": "asyncio.create_subprocess_exec",
        "subprocess.check_output": "asyncio.create_subprocess_exec with communicate()",
        "subprocess.Popen": "asyncio.create_subprocess_exec",
        "os.system": "asyncio.create_subprocess_shell",
        # File I/O
        "open": "aiofiles.open",
        # User input
        "input": "aioconsole.ainput or async stdin reader",
        # Database (common blocking ORMs)
        "cursor.execute": "async database driver (asyncpg, aiomysql, aiosqlite)",
        "connection.execute": "async database driver",
    }

    # Partial matches for module-level blocking patterns
    BLOCKING_PATTERNS: Dict[str, str] = {
        "requests.": "aiohttp or httpx async client",
        "urllib.": "aiohttp or httpx async client",
        "subprocess.": "asyncio subprocess APIs",
        "sqlite3.": "aiosqlite",
        "psycopg2.": "asyncpg",
        "pymysql.": "aiomysql",
    }

    THRESHOLDS = {
        "max_async_without_await": 0,  # Any async function without await is suspicious
    }

    def __init__(
        self,
        neo4j_client: DatabaseClient,
        detector_config: Optional[dict] = None,
        enricher: Optional[GraphEnricher] = None
    ):
        """Initialize async anti-pattern detector.

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
        self.max_async_without_await = config.get(
            "max_async_without_await",
            self.THRESHOLDS["max_async_without_await"]
        )

    def detect(self) -> List[Finding]:
        """Detect async anti-patterns in the codebase.

        Returns:
            List of findings for detected async anti-patterns
        """
        logger.info("Running AsyncAntipatternDetector")
        findings: List[Finding] = []

        # Detect blocking calls in async functions
        blocking_findings = self._find_blocking_calls_in_async()
        findings.extend(blocking_findings)
        logger.debug(f"Found {len(blocking_findings)} blocking call issues")

        # Detect wasteful async (async with no await)
        wasteful_findings = self._find_wasteful_async()
        findings.extend(wasteful_findings)
        logger.debug(f"Found {len(wasteful_findings)} wasteful async issues")

        logger.info(f"Found {len(findings)} async anti-pattern(s)")
        return findings

    def severity(self, finding: Finding) -> Severity:
        """Calculate severity based on anti-pattern type.

        Args:
            finding: Finding to assess

        Returns:
            Severity level
        """
        pattern_type = finding.graph_context.get("pattern_type", "")
        if pattern_type == "blocking_call":
            return Severity.HIGH
        elif pattern_type == "wasteful_async":
            return Severity.MEDIUM
        return Severity.LOW

    def _find_blocking_calls_in_async(self) -> List[Finding]:
        """Find async functions that call blocking functions.

        Queries for async functions that have CALLS relationships to
        known blocking functions like time.sleep, requests.get, etc.

        Returns:
            List of findings for blocking calls in async context
        """
        findings: List[Finding] = []

        # Build list of blocking call names for query
        blocking_names = list(self.BLOCKING_CALLS.keys())

        # Query for async functions calling blocking functions
        # Uses the function_async_yield_idx index on (is_async, has_yield)
        query = """
        MATCH (f:Function)-[c:CALLS]->(target)
        WHERE f.is_async = true
        OPTIONAL MATCH (file:File)-[:CONTAINS*]->(f)
        WITH f, c, target, file
        WHERE target.name IS NOT NULL
        RETURN f.qualifiedName AS func_name,
               f.name AS func_simple_name,
               f.filePath AS func_file,
               f.lineStart AS func_line,
               file.filePath AS containing_file,
               target.name AS call_name,
               c.line_number AS call_line,
               collect(DISTINCT target.name) AS all_calls
        ORDER BY f.qualifiedName
        """

        results = self.db.execute_query(query)

        # Group findings by function
        func_blocking_calls: Dict[str, Dict] = {}

        for record in results:
            func_name = record.get("func_name", "")
            call_name = record.get("call_name", "")

            if not func_name or not call_name:
                continue

            # Check if this is a blocking call
            blocking_alt = self._get_blocking_alternative(call_name)
            if not blocking_alt:
                continue

            if func_name not in func_blocking_calls:
                func_blocking_calls[func_name] = {
                    "func_simple_name": record.get("func_simple_name", ""),
                    "func_file": record.get("containing_file") or record.get("func_file", ""),
                    "func_line": record.get("func_line"),
                    "blocking_calls": [],
                }

            func_blocking_calls[func_name]["blocking_calls"].append({
                "call_name": call_name,
                "alternative": blocking_alt,
                "call_line": record.get("call_line"),
            })

        # Create findings for each function with blocking calls
        for func_name, data in func_blocking_calls.items():
            finding = self._create_blocking_call_finding(func_name, data)
            findings.append(finding)

        return findings

    def _get_blocking_alternative(self, call_name: str) -> Optional[str]:
        """Get the async alternative for a blocking call.

        Args:
            call_name: Name of the function being called

        Returns:
            Suggested async alternative, or None if not a blocking call
        """
        # Check exact match first
        if call_name in self.BLOCKING_CALLS:
            return self.BLOCKING_CALLS[call_name]

        # Check partial patterns
        for pattern, alternative in self.BLOCKING_PATTERNS.items():
            if call_name.startswith(pattern):
                return alternative

        return None

    def _create_blocking_call_finding(
        self,
        func_name: str,
        data: Dict
    ) -> Finding:
        """Create a finding for blocking calls in async function.

        Args:
            func_name: Qualified name of the async function
            data: Dict with function info and blocking calls

        Returns:
            Finding object
        """
        finding_id = str(uuid.uuid4())
        blocking_calls = data["blocking_calls"]
        file_path = data["func_file"] or ""

        # Format blocking calls for description
        calls_display = []
        for bc in blocking_calls[:5]:
            calls_display.append(f"- `{bc['call_name']}` → use `{bc['alternative']}`")
        if len(blocking_calls) > 5:
            calls_display.append(f"- ... and {len(blocking_calls) - 5} more")

        description = (
            f"Async function `{data['func_simple_name']}` calls blocking operations:\n\n"
            + "\n".join(calls_display) +
            "\n\nBlocking calls in async functions defeat the purpose of async/await "
            "and can block the entire event loop, causing performance issues."
        )

        # Determine severity based on number and type of blocking calls
        severity = Severity.HIGH if len(blocking_calls) >= 3 else Severity.MEDIUM

        # Build suggestion
        suggestion_lines = [
            "Replace blocking calls with async alternatives:\n"
        ]
        seen_alternatives: Set[str] = set()
        for bc in blocking_calls:
            alt = bc["alternative"]
            if alt not in seen_alternatives:
                suggestion_lines.append(f"- {bc['call_name']} → {alt}")
                seen_alternatives.add(alt)

        finding = Finding(
            id=finding_id,
            detector="AsyncAntipatternDetector",
            severity=severity,
            title=f"Blocking calls in async function: {data['func_simple_name']}",
            description=description,
            affected_nodes=[func_name],
            affected_files=[file_path] if file_path else [],
            line_start=data.get("func_line"),
            graph_context={
                "pattern_type": "blocking_call",
                "function_name": data["func_simple_name"],
                "blocking_calls": [bc["call_name"] for bc in blocking_calls],
                "alternatives": list(seen_alternatives),
                "call_count": len(blocking_calls),
            },
            suggested_fix="\n".join(suggestion_lines),
            estimated_effort=self._estimate_effort(len(blocking_calls)),
            created_at=datetime.now(),
        )

        # Add collaboration metadata
        confidence = 0.90  # High confidence - direct pattern match
        finding.add_collaboration_metadata(CollaborationMetadata(
            detector="AsyncAntipatternDetector",
            confidence=confidence,
            evidence=["blocking_call_in_async", f"call_count_{len(blocking_calls)}"],
            tags=["async_antipattern", "blocking_call", "performance"]
        ))

        # Flag entity for cross-detector collaboration
        if self.enricher:
            try:
                self.enricher.flag_entity(
                    entity_qualified_name=func_name,
                    detector="AsyncAntipatternDetector",
                    severity=severity.value,
                    issues=["blocking_call_in_async"],
                    confidence=confidence,
                    metadata={
                        "blocking_calls": [bc["call_name"] for bc in blocking_calls[:10]],
                        "call_count": len(blocking_calls)
                    }
                )
            except Exception:
                pass  # Don't fail detection if enrichment fails

        return finding

    def _find_wasteful_async(self) -> List[Finding]:
        """Find async functions that never use await.

        Async functions without await have unnecessary overhead and
        indicate a misunderstanding of async patterns.

        Returns:
            List of findings for wasteful async functions
        """
        findings: List[Finding] = []

        # Query for async functions with no CALLS to other async functions
        # and no await-related patterns
        # Uses the function_async_yield_idx index
        query = """
        MATCH (f:Function)
        WHERE f.is_async = true
          AND f.has_yield = false
        OPTIONAL MATCH (f)-[:CALLS]->(called:Function)
        WHERE called.is_async = true
        OPTIONAL MATCH (file:File)-[:CONTAINS*]->(f)
        WITH f, file, count(called) AS async_calls
        WHERE async_calls = 0
        RETURN f.qualifiedName AS func_name,
               f.name AS func_simple_name,
               f.filePath AS func_file,
               f.lineStart AS func_line,
               f.complexity AS complexity,
               file.filePath AS containing_file
        ORDER BY f.complexity DESC
        LIMIT 50
        """

        results = self.db.execute_query(query)

        for record in results:
            func_name = record.get("func_name", "")
            if not func_name:
                continue

            # Skip common patterns that are legitimately async without await
            func_simple_name = record.get("func_simple_name", "")
            if self._is_legitimate_async_without_await(func_simple_name):
                continue

            finding = self._create_wasteful_async_finding(record)
            findings.append(finding)

        return findings

    def _is_legitimate_async_without_await(self, func_name: str) -> bool:
        """Check if function is a legitimate async without await.

        Some patterns are legitimately async without explicit await:
        - Async context managers (__aenter__, __aexit__)
        - Async iterators (__anext__, __aiter__)
        - Factory functions that return awaitables
        - Test stubs/mocks

        Args:
            func_name: Simple function name

        Returns:
            True if this is a legitimate pattern
        """
        legitimate_patterns = {
            "__aenter__",
            "__aexit__",
            "__anext__",
            "__aiter__",
            "async_generator",
            "mock_",
            "stub_",
            "fake_",
        }

        if func_name in legitimate_patterns:
            return True

        for pattern in legitimate_patterns:
            if func_name.startswith(pattern) or func_name.endswith(pattern):
                return True

        return False

    def _create_wasteful_async_finding(self, record: Dict) -> Finding:
        """Create a finding for wasteful async function.

        Args:
            record: Query result record

        Returns:
            Finding object
        """
        finding_id = str(uuid.uuid4())
        func_name = record.get("func_name", "")
        func_simple_name = record.get("func_simple_name", "")
        file_path = record.get("containing_file") or record.get("func_file", "")
        complexity = record.get("complexity", 0)

        description = (
            f"Async function `{func_simple_name}` doesn't use `await` anywhere.\n\n"
            "This function has async overhead but doesn't perform any async operations. "
            "Either:\n"
            "1. Remove the `async` keyword if no async operations are needed\n"
            "2. Add proper async operations using `await`\n"
            "3. If this is intentional (e.g., for API compatibility), add a comment"
        )

        severity = Severity.MEDIUM

        finding = Finding(
            id=finding_id,
            detector="AsyncAntipatternDetector",
            severity=severity,
            title=f"Wasteful async: {func_simple_name} has no await",
            description=description,
            affected_nodes=[func_name],
            affected_files=[file_path] if file_path else [],
            line_start=record.get("func_line"),
            graph_context={
                "pattern_type": "wasteful_async",
                "function_name": func_simple_name,
                "complexity": complexity,
            },
            suggested_fix=(
                f"Option 1: Remove 'async' keyword if no async operations needed:\n"
                f"  - Change 'async def {func_simple_name}(...)' to 'def {func_simple_name}(...)'\n\n"
                f"Option 2: Add async operations if they should be async:\n"
                f"  - Use 'await' for async function calls\n"
                f"  - Use 'async for' for async iteration\n"
                f"  - Use 'async with' for async context managers"
            ),
            estimated_effort="Small (15-30 minutes)",
            created_at=datetime.now(),
        )

        # Add collaboration metadata
        confidence = 0.75  # Moderate confidence - may be intentional
        finding.add_collaboration_metadata(CollaborationMetadata(
            detector="AsyncAntipatternDetector",
            confidence=confidence,
            evidence=["async_without_await", "no_async_calls"],
            tags=["async_antipattern", "wasteful_async", "code_smell"]
        ))

        # Flag entity for cross-detector collaboration
        if self.enricher:
            try:
                self.enricher.flag_entity(
                    entity_qualified_name=func_name,
                    detector="AsyncAntipatternDetector",
                    severity=severity.value,
                    issues=["wasteful_async"],
                    confidence=confidence,
                    metadata={"complexity": complexity}
                )
            except Exception:
                pass

        return finding

    def _estimate_effort(self, blocking_call_count: int) -> str:
        """Estimate effort to fix based on number of blocking calls.

        Args:
            blocking_call_count: Number of blocking calls to replace

        Returns:
            Effort estimate string
        """
        if blocking_call_count >= 5:
            return "Medium (2-4 hours)"
        elif blocking_call_count >= 2:
            return "Small (1-2 hours)"
        else:
            return "Small (30 minutes)"

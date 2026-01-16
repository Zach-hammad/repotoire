"""Shared utilities for external tool-based detectors.

This module provides common functionality for detectors that wrap external tools
(bandit, ruff, mypy, pylint, etc.) to reduce code duplication.

Usage:
    from repotoire.detectors.external_tool_runner import (
        ExternalToolRunner,
        run_external_tool,
        get_graph_context,
    )

    class MyDetector(CodeSmellDetector):
        def detect(self):
            results = run_external_tool(
                cmd=["mytool", "-f", "json", str(self.repository_path)],
                tool_name="mytool",
                timeout=60,
                cwd=self.repository_path,
            )
            ...
"""

import json
import os
import subprocess
import threading
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, TypeVar, Union

from repotoire.logging_config import get_logger

logger = get_logger(__name__)

T = TypeVar("T")

# Cache for JS runtime detection with thread safety
_js_runtime_cache: Optional[str] = None
_js_runtime_lock = threading.Lock()


def get_js_runtime() -> str:
    """Detect available JavaScript runtime (bun or npm).

    Prefers Bun for performance when available, falls back to npm.
    Thread-safe with double-checked locking.

    Returns:
        "bun" or "npm"
    """
    global _js_runtime_cache

    # Fast path: check cache without lock
    if _js_runtime_cache is not None:
        return _js_runtime_cache

    # Slow path: acquire lock and check again
    with _js_runtime_lock:
        # Double-check after acquiring lock
        if _js_runtime_cache is not None:
            return _js_runtime_cache

        # Check for bun first (faster)
        try:
            result = subprocess.run(
                ["bun", "--version"],
                capture_output=True,
                text=True,
                timeout=5,
            )
            if result.returncode == 0:
                _js_runtime_cache = "bun"
                logger.debug(f"Using Bun runtime: {result.stdout.strip()}")
                return "bun"
        except (FileNotFoundError, subprocess.TimeoutExpired):
            pass

        # Check if npm is available before defaulting to it
        try:
            result = subprocess.run(
                ["npm", "--version"],
                capture_output=True,
                text=True,
                timeout=5,
            )
            if result.returncode == 0:
                _js_runtime_cache = "npm"
                logger.debug(f"Using npm runtime: {result.stdout.strip()}")
                return "npm"
        except (FileNotFoundError, subprocess.TimeoutExpired):
            pass

        # No JS runtime available - still return npm but log warning
        logger.warning("No JavaScript runtime (bun or npm) found. JS tool commands may fail.")
        _js_runtime_cache = "npm"
        return "npm"


def get_js_exec_command(package: str) -> List[str]:
    """Get command to execute a JS package binary.

    Uses bunx or npx depending on available runtime.

    Args:
        package: Package name to execute (e.g., "eslint", "tsc")

    Returns:
        Command list like ["bunx", "eslint"] or ["npx", "eslint"]
    """
    runtime = get_js_runtime()
    if runtime == "bun":
        return ["bunx", package]
    return ["npx", package]


def run_js_tool(
    package: str,
    args: List[str],
    tool_name: str,
    timeout: int = 120,
    cwd: Optional[Path] = None,
    env: Optional[Dict[str, str]] = None,
) -> "ExternalToolResult":
    """Run a JavaScript tool using the best available runtime.

    Automatically selects bun or npm based on availability.

    Args:
        package: JS package to run (e.g., "eslint", "tsc")
        args: Arguments to pass to the tool
        tool_name: Human-readable tool name for error messages
        timeout: Timeout in seconds (default: 120)
        cwd: Working directory for the tool
        env: Environment variables to pass

    Returns:
        ExternalToolResult with stdout, stderr, and status

    Example:
        result = run_js_tool(
            package="eslint",
            args=["--format", "json", "."],
            tool_name="eslint",
            timeout=120,
            cwd=repo_path,
        )
    """
    cmd = get_js_exec_command(package) + args
    return run_external_tool(
        cmd=cmd,
        tool_name=tool_name,
        timeout=timeout,
        cwd=cwd,
        env=env,
    )


class ExternalToolResult:
    """Result from running an external tool."""

    def __init__(
        self,
        success: bool,
        stdout: str = "",
        stderr: str = "",
        return_code: int = 0,
        timed_out: bool = False,
        error: Optional[Exception] = None,
    ):
        self.success = success
        self.stdout = stdout
        self.stderr = stderr
        self.return_code = return_code
        self.timed_out = timed_out
        self.error = error

    @property
    def json_output(self) -> Optional[Dict[str, Any]]:
        """Parse stdout as JSON if possible."""
        if not self.stdout:
            return None
        try:
            return json.loads(self.stdout)
        except json.JSONDecodeError:
            return None


def run_external_tool(
    cmd: List[str],
    tool_name: str,
    timeout: int = 120,
    cwd: Optional[Path] = None,
    env: Optional[Dict[str, str]] = None,
    check_installed: bool = True,
) -> ExternalToolResult:
    """Run an external tool with standard error handling.

    This is the primary entry point for running external linters/analyzers.
    Handles timeouts, missing tools, and common error cases.

    Args:
        cmd: Command and arguments to run
        tool_name: Human-readable tool name for error messages
        timeout: Timeout in seconds (default: 120)
        cwd: Working directory for the tool
        env: Environment variables to pass (merged with parent environment)
        check_installed: Whether to catch FileNotFoundError

    Returns:
        ExternalToolResult with stdout, stderr, and status

    Example:
        result = run_external_tool(
            cmd=["bandit", "-r", "-f", "json", str(repo_path)],
            tool_name="bandit",
            timeout=120,
            cwd=repo_path,
        )
        if result.success:
            data = result.json_output
            violations = data.get("results", [])
    """
    # Merge custom env with parent environment to preserve PATH, etc.
    # If env is None, subprocess.run inherits parent environment automatically
    # If env is provided, we need to merge it with os.environ
    effective_env = None
    if env is not None:
        effective_env = os.environ.copy()
        effective_env.update(env)

    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            cwd=cwd,
            timeout=timeout,
            env=effective_env,
        )
        return ExternalToolResult(
            success=True,
            stdout=result.stdout,
            stderr=result.stderr,
            return_code=result.returncode,
        )

    except subprocess.TimeoutExpired:
        logger.warning(f"{tool_name} timed out after {timeout}s")
        return ExternalToolResult(
            success=False,
            timed_out=True,
            error=TimeoutError(f"{tool_name} timed out after {timeout}s"),
        )

    except FileNotFoundError as e:
        if check_installed:
            # Provide appropriate install command based on tool type
            cmd_name = cmd[0] if cmd else tool_name
            if cmd_name in ("npx", "bunx", "npm", "bun", "yarn", "pnpm", "eslint", "tsc"):
                logger.error(f"{tool_name} not found. Install with: npm install -g {tool_name}")
            elif cmd_name in ("pip", "python", "bandit", "ruff", "mypy", "pylint"):
                logger.error(f"{tool_name} not found. Install with: pip install {tool_name}")
            else:
                logger.error(f"{tool_name} not found. Please install it first.")
        return ExternalToolResult(
            success=False,
            error=e,
        )

    except Exception as e:
        logger.error(f"Failed to run {tool_name}: {e}")
        return ExternalToolResult(
            success=False,
            error=e,
        )


def get_graph_context(
    graph_client,
    file_path: str,
    line: Optional[int] = None,
) -> Dict[str, Any]:
    """Get graph context for a file/line from the knowledge graph.

    Shared implementation for enriching findings with graph metadata.

    Args:
        graph_client: Database client (FalkorDB or Neo4j)
        file_path: Relative file path
        line: Optional line number to find containing entity

    Returns:
        Dict with keys: file_loc, language, affected_nodes, complexities
    """
    # Normalize path for consistent matching
    normalized_path = file_path.replace("\\", "/")

    # Build query based on whether we have a line number
    if line is not None:
        query = """
        MATCH (file:File {filePath: $file_path})
        OPTIONAL MATCH (file)-[:CONTAINS]->(entity)
        WHERE entity.lineStart <= $line AND entity.lineEnd >= $line
        RETURN file.loc as file_loc,
               file.language as language,
               collect(DISTINCT entity.qualifiedName) as affected_nodes,
               collect(DISTINCT entity.complexity) as complexities
        """
        params = {"file_path": normalized_path, "line": line}
    else:
        query = """
        MATCH (file:File {filePath: $file_path})
        RETURN file.loc as file_loc,
               file.language as language,
               [] as affected_nodes,
               [] as complexities
        """
        params = {"file_path": normalized_path}

    try:
        result = graph_client.execute_query(query, params)
        if result:
            record = result[0]
            return {
                "file_loc": record.get("file_loc"),
                "language": record.get("language"),
                "affected_nodes": record.get("affected_nodes", []),
                "complexities": [c for c in record.get("complexities", []) if c is not None],
            }
    except Exception as e:
        logger.debug(f"Could not get graph context for {file_path}: {e}")

    return {
        "file_loc": None,
        "language": None,
        "affected_nodes": [],
        "complexities": [],
    }


def batch_get_graph_context(
    graph_client,
    file_paths: List[str],
) -> Dict[str, Dict[str, Any]]:
    """Get graph context for multiple files in a single query.

    Performance: Uses UNWIND to fetch context in O(1) query instead of O(N).

    Args:
        graph_client: Database client
        file_paths: List of relative file paths

    Returns:
        Dict mapping file_path to context dict
    """
    if not file_paths:
        return {}

    # Normalize paths
    normalized_paths = [p.replace("\\", "/") for p in file_paths]

    query = """
    UNWIND $paths AS path
    MATCH (file:File {filePath: path})
    RETURN file.filePath as filePath,
           file.loc as file_loc,
           file.language as language
    """

    try:
        result = graph_client.execute_query(query, {"paths": normalized_paths})
        return {
            record["filePath"]: {
                "file_loc": record.get("file_loc"),
                "language": record.get("language"),
                "affected_nodes": [],
                "complexities": [],
            }
            for record in result
            if record.get("filePath")
        }
    except Exception as e:
        logger.debug(f"Could not batch get graph context: {e}")

    return {}


def estimate_fix_effort(severity_value: str) -> str:
    """Estimate fix effort based on severity.

    Args:
        severity_value: Severity level (critical, high, medium, low)

    Returns:
        Effort estimate string (e.g., "5 minutes", "30 minutes")
    """
    effort_map = {
        "critical": "30 minutes",
        "high": "15 minutes",
        "medium": "10 minutes",
        "low": "5 minutes",
    }
    return effort_map.get(severity_value.lower(), "10 minutes")


def get_category_tag(rule_code: str, tool_name: str) -> str:
    """Get a semantic category tag from a tool-specific rule code.

    Maps rule codes to human-readable categories.

    Args:
        rule_code: Tool-specific rule code (e.g., "B101", "E501", "C0301")
        tool_name: Name of the tool

    Returns:
        Category tag string
    """
    # Common category mappings across tools
    category_prefixes = {
        # Bandit
        "B1": "security/assert",
        "B2": "security/crypto",
        "B3": "security/injection",
        "B4": "security/permissions",
        "B5": "security/misc",
        "B6": "security/deserialization",
        "B7": "security/secrets",
        # Ruff/Flake8
        "E": "style/pep8",
        "W": "style/warning",
        "F": "logic/pyflakes",
        "C": "complexity",
        "I": "imports",
        "N": "naming",
        "D": "docstrings",
        "S": "security",
        "B": "bugbear",
        "A": "builtins",
        "T": "debugger",
        "UP": "upgrade",
        "PL": "pylint",
        # Pylint
        "C0": "convention",
        "R0": "refactor",
        "W0": "warning",
        "E0": "error",
        "F0": "fatal",
        # Mypy
        "assignment": "type/assignment",
        "arg-type": "type/argument",
        "return-value": "type/return",
        "union-attr": "type/optional",
        "import": "import",
    }

    rule_upper = rule_code.upper()

    # Try to find a matching prefix
    for prefix, category in category_prefixes.items():
        if rule_upper.startswith(prefix.upper()):
            return category

    # Default fallback
    return f"{tool_name}/{rule_code.lower()}"


class ExternalToolRunner:
    """Helper class for running external tools and processing results.

    Provides a fluent interface for configuring and running external tools.

    Example:
        runner = ExternalToolRunner(
            tool_name="bandit",
            graph_client=self.graph_client,
            repository_path=self.repository_path,
        )

        results = runner.run(
            cmd=["bandit", "-r", "-f", "json", str(self.repository_path)],
            timeout=120,
        )

        if results.success:
            findings = runner.process_json_results(
                results.json_output,
                result_key="results",
                processor=self._process_bandit_result,
            )
    """

    def __init__(
        self,
        tool_name: str,
        graph_client,
        repository_path: Path,
        enricher=None,
    ):
        """Initialize the runner.

        Args:
            tool_name: Human-readable tool name
            graph_client: Database client for context enrichment
            repository_path: Path to the repository being analyzed
            enricher: Optional GraphEnricher for persistent collaboration
        """
        self.tool_name = tool_name
        self.graph_client = graph_client
        self.repository_path = repository_path
        self.enricher = enricher

    def run(
        self,
        cmd: List[str],
        timeout: int = 120,
        env: Optional[Dict[str, str]] = None,
    ) -> ExternalToolResult:
        """Run the external tool.

        Args:
            cmd: Command and arguments
            timeout: Timeout in seconds
            env: Optional environment variables

        Returns:
            ExternalToolResult
        """
        return run_external_tool(
            cmd=cmd,
            tool_name=self.tool_name,
            timeout=timeout,
            cwd=self.repository_path,
            env=env,
        )

    def get_context(self, file_path: str, line: Optional[int] = None) -> Dict[str, Any]:
        """Get graph context for a file/line.

        Args:
            file_path: Relative file path
            line: Optional line number

        Returns:
            Context dict with file_loc, language, affected_nodes, complexities
        """
        return get_graph_context(self.graph_client, file_path, line)

    def process_json_results(
        self,
        json_output: Optional[Dict[str, Any]],
        result_key: str,
        processor: Callable[[Dict[str, Any]], T],
        max_results: int = 100,
    ) -> List[T]:
        """Process JSON output from external tool.

        Args:
            json_output: Parsed JSON output from tool
            result_key: Key in JSON containing results list
            processor: Function to process each result item
            max_results: Maximum results to process

        Returns:
            List of processed results
        """
        if not json_output:
            return []

        results = json_output.get(result_key, [])
        processed = []

        for item in results[:max_results]:
            try:
                result = processor(item)
                if result is not None:
                    processed.append(result)
            except Exception as e:
                logger.warning(f"Failed to process {self.tool_name} result: {e}")

        return processed

    def flag_entity(self, qualified_name: str, confidence: float = 0.85) -> None:
        """Flag an entity in the graph using the enricher.

        Args:
            qualified_name: Entity qualified name
            confidence: Confidence level (0-1)
        """
        if self.enricher:
            try:
                self.enricher.flag_entity(
                    qualified_name=qualified_name,
                    detector=self.tool_name,
                    confidence=confidence,
                )
            except Exception as e:
                logger.debug(f"Could not flag entity {qualified_name}: {e}")

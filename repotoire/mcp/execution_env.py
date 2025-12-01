"""Code execution environment setup for Repotoire MCP server.

Provides a pre-configured Jupyter-like environment where Claude can write
and execute Python code to interact with Repotoire, following Anthropic's
code execution MCP pattern.

Supports progressive discovery (REPO-208/209/213) with on-demand documentation.

Token-efficient utilities (REPO-210/211/212):
- Data filtering: Reduce 5000 tokens → 200 tokens (96% reduction)
- State persistence: Cache queries and store intermediate results
- Skill persistence: Save and reuse analysis functions across sessions
"""

import asyncio
import hashlib
import json
import os
import statistics
import sys
import threading
import time
import warnings
import zipfile
from collections import Counter
from datetime import datetime, timezone
from functools import wraps
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, TypeVar, Union

from repotoire.logging_config import get_logger

# Sandbox imports for secure skill execution (REPO-289)
from repotoire.sandbox import (
    SkillExecutor,
    SkillExecutorConfig,
    SkillResult,
    SkillSecurityError,
    SkillExecutionError,
    SkillTimeoutError,
)

logger = get_logger(__name__)

# =============================================================================
# REPO-210: Data Filtering Utilities
# =============================================================================
# Based on Anthropic's MCP best practices for preventing context bloat:
# "Agents can filter and transform large datasets in code before returning them,
#  preventing context bloat by only showing relevant information to the model."
# =============================================================================

T = TypeVar("T")


def _get_value(obj: Any, field: str, default: Any = None) -> Any:
    """Get a value from an object or dict by field name.

    Args:
        obj: Object or dict to get value from
        field: Field/key name
        default: Default value if not found

    Returns:
        Field value or default
    """
    if isinstance(obj, dict):
        return obj.get(field, default)
    return getattr(obj, field, default)


def summarize(
    results: List[T], fields: List[str], max_items: Optional[int] = None
) -> List[Dict[str, Any]]:
    """Return only specified fields from results, reducing token output.

    Token savings: Extract just the fields you need instead of full objects.
    Example: 100 entities × 50 fields → 100 entities × 3 fields = 94% reduction

    Args:
        results: List of objects or dicts to summarize
        fields: List of field names to extract
        max_items: Optional limit on number of items (None = all)

    Returns:
        List of dicts with only specified fields

    Examples:
        >>> results = search_code("auth", top_k=100)
        >>> # Full results: ~5000 tokens
        >>> # Summarized: ~200 tokens
        >>> summarize(results, ['name', 'file', 'score'])
        [{'name': 'login', 'file': 'auth.py', 'score': 0.95}, ...]

        >>> # With limit
        >>> summarize(results, ['name'], max_items=5)
        [{'name': 'login'}, {'name': 'logout'}, ...]
    """
    items = results[:max_items] if max_items else results
    return [{f: _get_value(r, f) for f in fields} for r in items]


def top_n(
    results: List[T],
    n: int = 5,
    sort_by: str = "score",
    reverse: bool = True,
) -> List[T]:
    """Return top N results sorted by field.

    Token savings: Get only the most relevant items.
    Example: 100 results → 5 results = 95% reduction

    Args:
        results: List of objects or dicts
        n: Number of results to return
        sort_by: Field name to sort by
        reverse: Sort descending if True (default: True for "top")

    Returns:
        Top N results sorted by field

    Examples:
        >>> top_n(functions, 5, 'complexity')
        [{'name': 'process_data', 'complexity': 45}, ...]

        >>> # Lowest complexity (ascending)
        >>> top_n(functions, 5, 'complexity', reverse=False)
        [{'name': 'simple_func', 'complexity': 1}, ...]
    """
    if not results:
        return []

    def key_func(x: T) -> Any:
        val = _get_value(x, sort_by, 0)
        # Handle None values
        if val is None:
            return float("-inf") if reverse else float("inf")
        return val

    return sorted(results, key=key_func, reverse=reverse)[:n]


def count_by(results: List[T], field: str) -> Dict[str, int]:
    """Group and count by field value.

    Token savings: Aggregate to summary stats instead of listing all items.
    Example: 100 findings → 3 severity counts = 97% reduction

    Args:
        results: List of objects or dicts
        field: Field name to group by

    Returns:
        Dict of {value: count}

    Examples:
        >>> count_by(findings, 'severity')
        {'HIGH': 12, 'MEDIUM': 34, 'LOW': 56}

        >>> count_by(entities, 'type')
        {'Function': 245, 'Class': 45, 'Module': 12}
    """
    values = [_get_value(r, field, "unknown") for r in results]
    # Convert non-hashable values to strings
    hashable_values = [str(v) if not isinstance(v, (str, int, float, bool, type(None))) else v for v in values]
    return dict(Counter(hashable_values))


def to_table(
    results: List[T],
    fields: List[str],
    max_width: int = 50,
    max_rows: Optional[int] = None,
) -> str:
    """Format results as markdown table for minimal token output.

    Token savings: Structured table format is more compact than JSON.

    Args:
        results: List of objects or dicts
        fields: List of field names for columns
        max_width: Maximum width per cell (truncates with ...)
        max_rows: Optional limit on rows (None = all)

    Returns:
        Markdown table string

    Examples:
        >>> print(to_table(results, ['name', 'file', 'score']))
        | name | file | score |
        |------|------|-------|
        | login | auth.py | 0.95 |
        | logout | auth.py | 0.89 |
    """

    def truncate(val: Any, width: int) -> str:
        s = str(val) if val is not None else ""
        return s[: width - 3] + "..." if len(s) > width else s

    items = results[:max_rows] if max_rows else results

    header = "| " + " | ".join(fields) + " |"
    separator = "|" + "|".join(["---" for _ in fields]) + "|"
    rows = []
    for r in items:
        values = [truncate(_get_value(r, f, ""), max_width) for f in fields]
        rows.append("| " + " | ".join(values) + " |")

    return "\n".join([header, separator] + rows)


def filter_by(results: List[T], **conditions: Union[Any, Callable[[Any], bool]]) -> List[T]:
    """Filter results by field conditions.

    Token savings: Pre-filter data before returning to LLM.

    Args:
        results: List of objects or dicts
        **conditions: Field=value conditions (supports callable for complex filters)

    Returns:
        Filtered list

    Examples:
        >>> # Simple equality
        >>> filter_by(results, severity='HIGH')
        [{'severity': 'HIGH', ...}, ...]

        >>> # With callable condition
        >>> filter_by(results, complexity=lambda x: x > 10)
        [{'complexity': 15, ...}, {'complexity': 22, ...}]

        >>> # Multiple conditions (AND)
        >>> filter_by(results, severity='HIGH', complexity=lambda x: x > 20)
    """
    filtered = []
    for r in results:
        match = True
        for field, condition in conditions.items():
            val = _get_value(r, field)
            if callable(condition):
                try:
                    if not condition(val):
                        match = False
                        break
                except (TypeError, ValueError):
                    match = False
                    break
            elif val != condition:
                match = False
                break
        if match:
            filtered.append(r)
    return filtered


def field_stats(results: List[T], field: str) -> Dict[str, Any]:
    """Calculate statistics for a numeric field.

    Token savings: Single stats dict instead of raw data.
    Example: 100 complexity values → 6 summary stats = 94% reduction

    Args:
        results: List of objects or dicts
        field: Numeric field name

    Returns:
        Dict with min, max, mean, median, sum, count

    Examples:
        >>> field_stats(functions, 'complexity')
        {'min': 1, 'max': 45, 'mean': 8.2, 'median': 5, 'sum': 820, 'count': 100}

        >>> field_stats(functions, 'lines_of_code')
        {'min': 5, 'max': 500, 'mean': 45.2, 'median': 30, 'sum': 4520, 'count': 100}
    """
    values = [_get_value(r, field, 0) for r in results]
    values = [v for v in values if isinstance(v, (int, float)) and v is not None]

    if not values:
        return {"min": 0, "max": 0, "mean": 0, "median": 0, "sum": 0, "count": 0}

    return {
        "min": min(values),
        "max": max(values),
        "mean": round(statistics.mean(values), 2),
        "median": statistics.median(values),
        "sum": sum(values),
        "count": len(values),
    }


def group_by(results: List[T], field: str) -> Dict[str, List[T]]:
    """Group results by field value.

    Args:
        results: List of objects or dicts
        field: Field name to group by

    Returns:
        Dict mapping field values to lists of matching items

    Examples:
        >>> grouped = group_by(findings, 'severity')
        >>> len(grouped['HIGH'])  # Count high severity
        12
        >>> grouped['CRITICAL']  # Get all critical findings
        [{'severity': 'CRITICAL', 'message': '...'}, ...]
    """
    groups: Dict[str, List[T]] = {}
    for r in results:
        key = str(_get_value(r, field, "unknown"))
        if key not in groups:
            groups[key] = []
        groups[key].append(r)
    return groups


# =============================================================================
# REPO-211: State Persistence
# =============================================================================
# Based on Anthropic's MCP best practices:
# "Intermediate results stay within the execution environment by default"
# "LLMs lack state, requiring code to manage state within memory"
# =============================================================================

# Module-level state (persists across exec() calls within same kernel session)
_state: Dict[str, Any] = {}
_cache: Dict[str, Dict[str, Any]] = {}
_state_lock = threading.Lock()
_cache_lock = threading.Lock()


def store(key: str, value: Any) -> None:
    """Store a value that persists across code blocks in the same session.

    Use this to save intermediate results that you'll need later, avoiding
    re-computation and reducing token usage.

    Args:
        key: Unique identifier for the value
        value: Any Python object to store

    Examples:
        >>> # First code block: run expensive query
        >>> results = query("MATCH (f:Function) RETURN f LIMIT 100")
        >>> store('all_functions', results)

        >>> # Later code block: reuse without re-querying
        >>> functions = get('all_functions')
        >>> critical = [f for f in functions if f['complexity'] > 30]
    """
    with _state_lock:
        _state[key] = value


def get(key: str, default: Any = None) -> Any:
    """Retrieve a stored value.

    Args:
        key: Identifier used in store()
        default: Value to return if key not found

    Returns:
        Stored value or default

    Examples:
        >>> functions = get('all_functions')
        >>> functions = get('missing_key', [])  # Returns empty list
    """
    with _state_lock:
        return _state.get(key, default)


def delete(key: str) -> bool:
    """Delete a stored value.

    Args:
        key: Identifier to delete

    Returns:
        True if key existed and was deleted, False otherwise
    """
    with _state_lock:
        if key in _state:
            del _state[key]
            return True
        return False


def list_stored() -> List[str]:
    """List all stored keys.

    Returns:
        List of key names

    Examples:
        >>> store('functions', [...])
        >>> store('findings', [...])
        >>> list_stored()
        ['functions', 'findings']
    """
    with _state_lock:
        return list(_state.keys())


def clear_state() -> int:
    """Clear all stored state.

    Returns:
        Number of items cleared

    Examples:
        >>> clear_state()
        5  # Cleared 5 stored items
    """
    with _state_lock:
        count = len(_state)
        _state.clear()
        return count


def cache_query(
    key: str, cypher: str, params: Optional[Dict] = None, ttl: int = 300
) -> Any:
    """Execute query with caching. Reuse results within TTL.

    Token savings: Avoid re-running expensive queries within the same session.

    Args:
        key: Cache key for this query
        cypher: Cypher query string
        params: Query parameters
        ttl: Time-to-live in seconds (default: 5 minutes)

    Returns:
        Query results (from cache if available and fresh)

    Examples:
        >>> # First call executes query
        >>> funcs = cache_query('complex_funcs', '''
        ...     MATCH (f:Function) WHERE f.complexity > 20 RETURN f
        ... ''')

        >>> # Second call uses cache (no re-query!)
        >>> funcs = cache_query('complex_funcs', '''
        ...     MATCH (f:Function) WHERE f.complexity > 20 RETURN f
        ... ''')
    """
    with _cache_lock:
        cached = _cache.get(key)
        if cached and time.time() - cached["time"] < ttl:
            return cached["data"]

    # Execute query (assumes global 'query' function from startup script)
    # This will be available in the execution context
    import builtins

    query_func = getattr(builtins, "query", None)
    if query_func is None:
        raise RuntimeError("Query function not available. Ensure startup script has run.")

    result = query_func(cypher, params or {})

    with _cache_lock:
        _cache[key] = {"data": result, "time": time.time()}

    return result


def invalidate_cache(key: Optional[str] = None) -> int:
    """Invalidate cache entry or all cache.

    Args:
        key: Specific key to invalidate, or None for all

    Returns:
        Number of entries invalidated

    Examples:
        >>> invalidate_cache('complex_funcs')  # Invalidate one
        1
        >>> invalidate_cache()  # Invalidate all
        5
    """
    with _cache_lock:
        if key:
            if key in _cache:
                del _cache[key]
                return 1
            return 0
        else:
            count = len(_cache)
            _cache.clear()
            return count


def cache_info() -> Dict[str, Any]:
    """Get cache statistics.

    Returns:
        Dict with cache info: keys, sizes, ages

    Examples:
        >>> cache_info()
        {
            'keys': ['complex_funcs', 'all_classes'],
            'count': 2,
            'entries': {
                'complex_funcs': {'age_seconds': 45.2, 'size': 1234},
                'all_classes': {'age_seconds': 120.5, 'size': 5678}
            }
        }
    """
    with _cache_lock:
        now = time.time()
        return {
            "keys": list(_cache.keys()),
            "count": len(_cache),
            "entries": {
                k: {"age_seconds": round(now - v["time"], 1), "size": len(str(v["data"]))}
                for k, v in _cache.items()
            },
        }


def cached(key: Optional[str] = None, ttl: int = 300) -> Callable:
    """Decorator to cache function results.

    Args:
        key: Cache key (defaults to function name)
        ttl: Time-to-live in seconds

    Returns:
        Decorated function with caching

    Examples:
        >>> @cached('complex_functions', ttl=600)
        ... def get_complex_functions():
        ...     return query("MATCH (f:Function) WHERE f.complexity > 20 RETURN f")

        >>> # First call computes
        >>> result = get_complex_functions()
        >>> # Second call uses cache
        >>> result = get_complex_functions()
    """

    def decorator(func: Callable) -> Callable:
        @wraps(func)
        def wrapper(*args: Any, **kwargs: Any) -> Any:
            cache_key = key or func.__name__
            with _cache_lock:
                cached_entry = _cache.get(cache_key)
                if cached_entry and time.time() - cached_entry["time"] < ttl:
                    return cached_entry["data"]

            result = func(*args, **kwargs)

            with _cache_lock:
                _cache[cache_key] = {"data": result, "time": time.time()}

            return result

        return wrapper

    return decorator


# =============================================================================
# REPO-212: Skill Persistence
# =============================================================================
# Based on Anthropic's MCP best practices:
# "Agents can persist their own code as reusable functions and skills,
#  allowing them to save implementations for future use and build a toolbox
#  of higher-level capabilities."
# =============================================================================

SKILLS_DIR = Path.home() / ".repotoire" / "skills"


def save_skill(
    name: str,
    code: str,
    description: str = "",
    tags: Optional[List[str]] = None,
    overwrite: bool = False,
) -> str:
    """Save code as a reusable skill that persists across sessions.

    Skills are stored in ~/.repotoire/skills/ and can be loaded in future
    sessions, building a personal library of analysis functions.

    Args:
        name: Unique skill name (alphanumeric + underscore only)
        code: Python code to save
        description: What the skill does
        tags: Optional categorization tags for searching
        overwrite: Allow overwriting existing skill

    Returns:
        Path to saved skill file

    Raises:
        ValueError: If name invalid or skill exists without overwrite

    Examples:
        >>> save_skill('find_god_classes', '''
        ... def find_god_classes(threshold=10):
        ...     \"\"\"Find classes with too many methods.\"\"\"
        ...     return query(f\"\"\"
        ...         MATCH (c:Class)-[:CONTAINS]->(m:Function)
        ...         WITH c, count(m) as method_count
        ...         WHERE method_count > {threshold}
        ...         RETURN c.qualifiedName, method_count
        ...         ORDER BY method_count DESC
        ...     \"\"\")
        ... ''', description='Find classes with excessive methods',
        ...     tags=['code-smell', 'classes'])
        '~/.repotoire/skills/find_god_classes.py'
    """
    # Validate name
    if not name or not name.replace("_", "").isalnum():
        raise ValueError(f"Invalid skill name: '{name}'. Use alphanumeric + underscore only.")

    try:
        SKILLS_DIR.mkdir(parents=True, exist_ok=True)
    except PermissionError as e:
        raise PermissionError(f"Cannot create skills directory: {e}") from e

    skill_file = SKILLS_DIR / f"{name}.py"
    meta_file = SKILLS_DIR / f"{name}.json"

    if skill_file.exists() and not overwrite:
        raise ValueError(f"Skill '{name}' already exists. Use overwrite=True to replace.")

    # Write code
    header = f'"""{description}"""\n\n' if description else ""
    try:
        skill_file.write_text(header + code, encoding="utf-8")
    except PermissionError as e:
        raise PermissionError(f"Cannot write skill file: {e}") from e

    # Write metadata
    now = datetime.now().isoformat()
    metadata = {
        "name": name,
        "description": description,
        "tags": tags or [],
        "created": now if not meta_file.exists() else _load_skill_metadata(name).get("created", now),
        "updated": now,
    }
    meta_file.write_text(json.dumps(metadata, indent=2), encoding="utf-8")

    return str(skill_file)


def _load_skill_metadata(name: str) -> Dict[str, Any]:
    """Load skill metadata from JSON file."""
    meta_file = SKILLS_DIR / f"{name}.json"
    if meta_file.exists():
        try:
            return json.loads(meta_file.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, IOError):
            return {}
    return {}


def load_skill(name: str, globals_dict: Optional[Dict] = None) -> None:
    """Load a saved skill definition for later secure execution.

    SECURITY NOTE (REPO-289): This function NO LONGER executes skill code on the host.
    Instead, it loads the skill definition and registers it for sandboxed execution
    via execute_skill_secure().

    For backward compatibility, skill functions are added as wrappers that
    execute securely in the E2B sandbox.

    Args:
        name: Name of the skill to load
        globals_dict: Optional globals dict to add skill wrapper to (default: caller's globals)

    Raises:
        FileNotFoundError: If skill doesn't exist
        SkillSecurityError: If sandbox is not configured (E2B_API_KEY required)

    Examples:
        >>> load_skill('find_god_classes')
        ✓ Loaded skill: find_god_classes (secure sandbox mode)

        >>> # The skill function is now a secure wrapper
        >>> result = await find_god_classes(threshold=15)

    Note:
        Skills now execute asynchronously in a sandbox. If you need synchronous
        execution, use asyncio.run():

        >>> result = asyncio.run(find_god_classes(threshold=15))
    """
    skill_file = SKILLS_DIR / f"{name}.py"
    if not skill_file.exists():
        available = list_skills()
        available_str = ", ".join(available) if available else "none"
        raise FileNotFoundError(f"Skill '{name}' not found. Available: {available_str}")

    code = skill_file.read_text(encoding="utf-8")

    # Use provided globals or get caller's globals
    if globals_dict is None:
        import inspect

        frame = inspect.currentframe()
        if frame and frame.f_back:
            globals_dict = frame.f_back.f_globals
        else:
            globals_dict = globals()

    # SECURITY (REPO-289): Instead of exec(), register skill for sandboxed execution
    # Parse the skill code to find function definitions
    import ast
    try:
        tree = ast.parse(code)
    except SyntaxError as e:
        raise SkillExecutionError(
            f"Skill '{name}' has syntax errors: {e}",
            skill_name=name,
            error_type="SyntaxError",
        )

    # Find all function definitions in the skill
    functions_found = []
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef):
            func_name = node.name
            functions_found.append(func_name)

            # Create a secure wrapper for each function
            secure_wrapper = _create_secure_skill_wrapper(code, func_name, name)
            globals_dict[func_name] = secure_wrapper

    if not functions_found:
        warnings.warn(
            f"Skill '{name}' defines no functions. Nothing was loaded.",
            UserWarning,
        )
        return

    logger.info(
        f"Loaded skill '{name}' with functions: {functions_found} (secure sandbox mode)",
        extra={"skill_name": name, "functions": functions_found},
    )


def _create_secure_skill_wrapper(
    skill_code: str,
    func_name: str,
    skill_name: str,
) -> Callable:
    """Create a wrapper function that executes a skill securely in sandbox.

    Args:
        skill_code: Full Python code of the skill
        func_name: Name of the function to wrap
        skill_name: Human-readable skill name for logging

    Returns:
        Async callable that executes the skill in sandbox
    """
    async def secure_skill_wrapper(**kwargs: Any) -> Any:
        """Execute skill function securely in E2B sandbox.

        This wrapper ensures all skill execution happens in an isolated
        sandbox environment, preventing arbitrary code execution on the host.
        """
        config = SkillExecutorConfig(
            timeout_seconds=300,  # 5 minute default
            memory_mb=1024,  # 1GB default
            enable_audit_log=True,
        )

        try:
            async with SkillExecutor(config) as executor:
                result = await executor.execute_skill(
                    skill_code=skill_code,
                    skill_name=func_name,
                    context=kwargs,
                )

                if not result.success:
                    raise SkillExecutionError(
                        result.error or "Skill execution failed",
                        skill_name=func_name,
                        error_type=result.error_type,
                        traceback=result.traceback,
                    )

                return result.result

        except SkillSecurityError:
            # Re-raise security errors with helpful message
            raise SkillSecurityError(
                f"Cannot execute skill '{func_name}': sandbox not configured",
                skill_name=func_name,
                suggestion="Set E2B_API_KEY environment variable for secure skill execution. "
                "Local exec() is disabled for security.",
            )

    # Preserve function metadata
    secure_skill_wrapper.__name__ = func_name
    secure_skill_wrapper.__doc__ = f"Secure sandboxed skill: {skill_name}.{func_name}"

    return secure_skill_wrapper


def list_skills(tag: Optional[str] = None) -> List[str]:
    """List all saved skills, optionally filtered by tag.

    Args:
        tag: Optional tag to filter by

    Returns:
        List of skill names

    Examples:
        >>> list_skills()
        ['find_god_classes', 'analyze_imports', 'check_naming']

        >>> list_skills(tag='code-smell')
        ['find_god_classes', 'find_long_methods']
    """
    if not SKILLS_DIR.exists():
        return []

    skills = []
    for f in SKILLS_DIR.glob("*.py"):
        name = f.stem
        if tag:
            meta = _load_skill_metadata(name)
            if tag in meta.get("tags", []):
                skills.append(name)
        else:
            skills.append(name)

    return sorted(skills)


def skill_info(name: str) -> Dict[str, Any]:
    """Get detailed information about a skill.

    Args:
        name: Skill name

    Returns:
        Dict with skill metadata and code preview

    Raises:
        FileNotFoundError: If skill doesn't exist

    Examples:
        >>> skill_info('find_god_classes')
        {
            'name': 'find_god_classes',
            'description': 'Find classes with excessive methods',
            'tags': ['code-smell', 'classes'],
            'created': '2024-01-15T10:30:00',
            'updated': '2024-01-16T14:22:00',
            'preview': 'def find_god_classes(threshold=10):...',
            'lines': 15,
            'size_bytes': 456
        }
    """
    skill_file = SKILLS_DIR / f"{name}.py"

    if not skill_file.exists():
        raise FileNotFoundError(f"Skill '{name}' not found")

    code = skill_file.read_text(encoding="utf-8")
    lines = code.split("\n")

    info: Dict[str, Any] = {
        "name": name,
        "preview": "\n".join(lines[:15]) + ("\n..." if len(lines) > 15 else ""),
        "lines": len(lines),
        "size_bytes": len(code.encode("utf-8")),
    }

    # Merge metadata
    meta = _load_skill_metadata(name)
    info.update(meta)

    return info


def delete_skill(name: str) -> bool:
    """Delete a saved skill.

    Args:
        name: Skill name to delete

    Returns:
        True if deleted, False if not found

    Examples:
        >>> delete_skill('old_skill')
        True
        >>> delete_skill('nonexistent')
        False
    """
    skill_file = SKILLS_DIR / f"{name}.py"
    meta_file = SKILLS_DIR / f"{name}.json"

    deleted = False
    if skill_file.exists():
        skill_file.unlink()
        deleted = True
    if meta_file.exists():
        meta_file.unlink()

    return deleted


def search_skills(query_str: str) -> List[Dict[str, Any]]:
    """Search skills by name, description, or tags.

    Args:
        query_str: Search term (case-insensitive)

    Returns:
        List of matching skill info dicts

    Examples:
        >>> search_skills('class')
        [
            {'name': 'find_god_classes', 'description': 'Find classes...', ...},
            {'name': 'class_metrics', 'description': 'Calculate class...', ...}
        ]
    """
    query_lower = query_str.lower()
    results = []

    for name in list_skills():
        info = skill_info(name)
        if (
            query_lower in name.lower()
            or query_lower in info.get("description", "").lower()
            or any(query_lower in tag.lower() for tag in info.get("tags", []))
        ):
            results.append(info)

    return results


def export_skills(path: Optional[str] = None) -> str:
    """Export all skills to a zip archive.

    Args:
        path: Output path (default: ~/.repotoire/skills_export.zip)

    Returns:
        Path to export file

    Examples:
        >>> export_skills()
        '~/.repotoire/skills_export.zip'
        >>> export_skills('/tmp/my_skills.zip')
        '/tmp/my_skills.zip'
    """
    if path is None:
        path = str(SKILLS_DIR.parent / "skills_export.zip")

    if not SKILLS_DIR.exists():
        raise FileNotFoundError("No skills directory found")

    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as zf:
        for f in SKILLS_DIR.glob("*"):
            if f.is_file():
                zf.write(f, f.name)

    return path


def import_skills(path: str, overwrite: bool = False) -> int:
    """Import skills from a zip archive.

    Args:
        path: Path to skills archive
        overwrite: Allow overwriting existing skills

    Returns:
        Number of skill files imported

    Examples:
        >>> import_skills('/tmp/shared_skills.zip')
        4
        >>> import_skills('/tmp/shared_skills.zip', overwrite=True)
        4
    """
    SKILLS_DIR.mkdir(parents=True, exist_ok=True)
    count = 0

    with zipfile.ZipFile(path, "r") as zf:
        for name in zf.namelist():
            dest = SKILLS_DIR / name
            if dest.exists() and not overwrite:
                continue
            dest.write_bytes(zf.read(name))
            count += 1

    return count


def get_skills_directory() -> Path:
    """Get the skills directory path.

    Returns:
        Path to skills directory (~/.repotoire/skills/)
    """
    return SKILLS_DIR


# API documentation - loaded on-demand via resources
API_DOCUMENTATION = """# Repotoire Code Execution API

## Pre-configured Objects

### `client: Neo4jClient`
Connected Neo4j client for graph database operations.

**Properties:**
- `client.uri`: Connection URI
- `client.driver`: Neo4j driver instance

**Methods:**
- `client.execute_query(cypher, params)`: Execute Cypher query
- `client.batch_create_nodes(entities)`: Batch create nodes
- `client.batch_create_relationships(relationships)`: Batch create relationships
- `client.close()`: Close connection

### `rule_engine: RuleEngine`
Engine for managing and executing custom quality rules.

**Methods:**
- `rule_engine.list_rules(enabled_only=True)`: List all rules
- `rule_engine.get_rule(rule_id)`: Get specific rule
- `rule_engine.execute_rule(rule)`: Execute a rule
- `rule_engine.get_hot_rules(top_k=10)`: Get high-priority rules

## Core Utility Functions

### `query(cypher: str, params: Dict = None) -> List[Dict]`
Execute a Cypher query and return results.

```python
results = query('''
    MATCH (c:Class)
    WHERE c.complexity > 50
    RETURN c.qualifiedName, c.complexity
    LIMIT 5
''')
```

### `search_code(query_text: str, top_k: int = 10, entity_types: List[str] = None)`
Search codebase using vector similarity.

```python
results = search_code("authentication functions", top_k=5)
for result in results:
    print(f"{result.qualified_name}: {result.similarity_score}")
```

### `list_rules(enabled_only: bool = True) -> List[Rule]`
List all custom quality rules.

### `execute_rule(rule_id: str) -> List[Finding]`
Execute a custom rule by ID.

### `codebase_stats()`
Print quick statistics about the codebase.

---

## Data Filtering (REPO-210) - Reduce Token Usage by 96%

These utilities filter/transform data BEFORE returning to LLM, preventing context bloat.

### `summarize(results, fields, max_items=None) -> List[Dict]`
Extract only specified fields from results.

```python
# Full results: ~5000 tokens → Summarized: ~200 tokens
results = search_code("auth", top_k=100)
summary = summarize(results, ['name', 'file', 'score'], max_items=10)
```

### `top_n(results, n=5, sort_by='score', reverse=True) -> List`
Get top N results sorted by field.

```python
# Get 5 most complex functions
complex = top_n(functions, 5, 'complexity')
```

### `count_by(results, field) -> Dict[str, int]`
Aggregate counts by field value.

```python
# 100 findings → 3 severity counts = 97% token reduction
count_by(findings, 'severity')  # {'HIGH': 12, 'MEDIUM': 34, 'LOW': 56}
```

### `to_table(results, fields, max_width=50, max_rows=None) -> str`
Format as markdown table (compact output).

```python
print(to_table(results, ['name', 'complexity', 'file']))
# | name | complexity | file |
# |------|------------|------|
# | process | 45 | data.py |
```

### `filter_by(results, **conditions) -> List`
Filter by field conditions (supports lambdas).

```python
filter_by(results, severity='HIGH')
filter_by(results, complexity=lambda x: x > 20)
```

### `field_stats(results, field) -> Dict`
Calculate statistics for numeric field.

```python
field_stats(functions, 'complexity')
# {'min': 1, 'max': 45, 'mean': 8.2, 'median': 5, 'sum': 820, 'count': 100}
```

### `group_by(results, field) -> Dict[str, List]`
Group results by field value.

```python
grouped = group_by(findings, 'severity')
len(grouped['HIGH'])  # 12
```

---

## State Persistence (REPO-211) - Cache Across Code Blocks

Store intermediate results to avoid re-computation and re-querying.

### `store(key, value)` / `get(key, default=None)`
Persist values across code blocks in the same session.

```python
# Block 1: Run expensive query
results = query("MATCH (f:Function) RETURN f LIMIT 1000")
store('all_functions', results)

# Block 2: Reuse without re-querying
functions = get('all_functions')
```

### `list_stored()` / `delete(key)` / `clear_state()`
Manage stored values.

```python
list_stored()  # ['all_functions', 'findings']
delete('old_data')
clear_state()  # Clear all
```

### `cache_query(key, cypher, params=None, ttl=300)`
Execute query with automatic caching (TTL in seconds).

```python
# First call executes query
funcs = cache_query('complex', 'MATCH (f) WHERE f.complexity > 20 RETURN f')
# Second call uses cache (no re-query!)
funcs = cache_query('complex', 'MATCH (f) WHERE f.complexity > 20 RETURN f')
```

### `@cached(key=None, ttl=300)`
Decorator to cache function results.

```python
@cached('complex_funcs', ttl=600)
def get_complex_functions():
    return query("MATCH (f:Function) WHERE f.complexity > 20 RETURN f")
```

### `cache_info()` / `invalidate_cache(key=None)`
Manage query cache.

```python
cache_info()  # {'keys': [...], 'count': 2, 'entries': {...}}
invalidate_cache('complex')  # Invalidate one
invalidate_cache()  # Invalidate all
```

---

## Skill Persistence (REPO-212) - Reusable Analysis Functions

Save analysis functions that persist across sessions in ~/.repotoire/skills/

### `save_skill(name, code, description='', tags=None, overwrite=False)`
Save code as a reusable skill.

```python
save_skill('find_god_classes', '''
def find_god_classes(threshold=10):
    return query(f\"\"\"
        MATCH (c:Class)-[:CONTAINS]->(m:Function)
        WITH c, count(m) as method_count
        WHERE method_count > {threshold}
        RETURN c.qualifiedName, method_count
        ORDER BY method_count DESC
    \"\"\")
''', description='Find classes with too many methods',
    tags=['code-smell', 'classes'])
```

### `load_skill(name)`
Load a saved skill into the current session.

```python
load_skill('find_god_classes')
god_classes = find_god_classes(threshold=15)  # Function now available!
```

### `list_skills(tag=None)` / `skill_info(name)` / `search_skills(query)`
Discover and search saved skills.

```python
list_skills()  # ['find_god_classes', 'analyze_imports']
list_skills(tag='code-smell')  # Filter by tag
skill_info('find_god_classes')  # Full details
search_skills('class')  # Search by name/description/tags
```

### `delete_skill(name)` / `export_skills(path)` / `import_skills(path)`
Manage skill library.

```python
delete_skill('old_skill')
export_skills('/tmp/my_skills.zip')  # Share with team
import_skills('/tmp/shared_skills.zip')
```

---

## Available Models

All Repotoire models are imported:
- `CodebaseHealth`, `Finding`, `Severity`
- `File`, `Class`, `Function`, `Module`, `Rule`
- `GraphRAGRetriever`, `CodeEmbedder`

## Environment Variables

Pre-configured:
- `REPOTOIRE_NEO4J_URI`: bolt://localhost:7688
- `REPOTOIRE_NEO4J_PASSWORD`: From env or default
"""


def get_api_documentation() -> str:
    """Get complete API documentation for code execution.

    Returns:
        Markdown documentation string
    """
    return API_DOCUMENTATION

# Startup script that runs when the execution environment initializes
REPOTOIRE_STARTUP_SCRIPT = """
# Repotoire Code Execution Environment
# This environment is pre-configured with Repotoire imports and utilities
# Includes token-efficient utilities (REPO-210/211/212)

import sys
import os
from pathlib import Path
from datetime import datetime, timezone
from typing import Optional, List, Dict, Any

# Repotoire imports
from repotoire.graph.client import Neo4jClient
from repotoire.models import (
    CodebaseHealth, Finding, Severity,
    File, Class, Function, Module, Rule
)
from repotoire.rules.engine import RuleEngine
from repotoire.rules.validator import RuleValidator
from repotoire.ai.retrieval import GraphRAGRetriever
from repotoire.ai.embeddings import CodeEmbedder

# Import token-efficient utilities (REPO-210/211/212)
from repotoire.mcp.execution_env import (
    # REPO-210: Data Filtering
    summarize, top_n, count_by, to_table, filter_by, field_stats, group_by,
    # REPO-211: State Persistence
    store, get, delete, list_stored, clear_state,
    cache_query, invalidate_cache, cache_info, cached,
    # REPO-212: Skill Persistence
    save_skill, load_skill, list_skills, skill_info, delete_skill,
    search_skills, export_skills, import_skills, get_skills_directory, SKILLS_DIR,
)

# Connection helpers
def connect_neo4j(
    uri: str = "bolt://localhost:7688",
    password: str = None
) -> Neo4jClient:
    \"\"\"Quick Neo4j connection helper.\"\"\"
    if password is None:
        password = os.getenv("REPOTOIRE_NEO4J_PASSWORD", "falkor-password")
    return Neo4jClient(uri=uri, password=password)

# Pre-connect client for convenience
try:
    client = connect_neo4j()
    print("✓ Connected to Neo4j")
except Exception as e:
    print(f"⚠️  Neo4j connection failed: {e}")
    print("   Use: client = connect_neo4j(uri='...', password='...')")
    client = None

# Initialize common tools
if client:
    try:
        rule_engine = RuleEngine(client)
        print("✓ Rule engine initialized")
    except Exception as e:
        print(f"⚠️  Rule engine init failed: {e}")
        rule_engine = None

# Utility functions
def query(cypher: str, params: Dict[str, Any] = None) -> List[Dict]:
    \"\"\"Execute a Cypher query and return results.

    Example:
        results = query("MATCH (f:Function) WHERE f.complexity > 20 RETURN f LIMIT 10")
    \"\"\"
    if client is None:
        raise RuntimeError("Not connected to Neo4j. Use: client = connect_neo4j()")
    return client.execute_query(cypher, params or {})

def search_code(
    query_text: str,
    top_k: int = 10,
    entity_types: Optional[List[str]] = None
):
    \"\"\"Search codebase using vector similarity.

    Example:
        results = search_code("authentication functions", top_k=5)
    \"\"\"
    embedder = CodeEmbedder()
    retriever = GraphRAGRetriever(client, embedder)
    return retriever.retrieve(query_text, top_k=top_k, entity_types=entity_types)

def list_rules(enabled_only: bool = True):
    \"\"\"List all custom rules.

    Example:
        rules = list_rules()
        for rule in rules:
            print(f"{rule.id}: {rule.name} (priority: {rule.calculate_priority():.1f})")
    \"\"\"
    if rule_engine is None:
        raise RuntimeError("Rule engine not initialized")
    return rule_engine.list_rules(enabled_only=enabled_only)

def execute_rule(rule_id: str):
    \"\"\"Execute a custom rule by ID.

    Example:
        findings = execute_rule("no-god-classes")
        print(f"Found {len(findings)} issues")
    \"\"\"
    if rule_engine is None:
        raise RuntimeError("Rule engine not initialized")
    rule = rule_engine.get_rule(rule_id)
    if not rule:
        raise ValueError(f"Rule '{rule_id}' not found")
    return rule_engine.execute_rule(rule)

# Quick stats
def codebase_stats():
    \"\"\"Print quick stats about the codebase.\"\"\"
    if client is None:
        print("Not connected to Neo4j")
        return

    counts = query(\"\"\"
        MATCH (n)
        RETURN
            labels(n)[0] as type,
            count(n) as count
        ORDER BY count DESC
    \"\"\")

    print("\\nCodebase Statistics:")
    print("-" * 40)
    for row in counts:
        print(f"{row['type']:20s} {row['count']:>10,}")

# Alias for backwards compatibility
stats = codebase_stats

print("\\n" + "=" * 60)
print("Repotoire Code Execution Environment")
print("=" * 60)
print("\\nPre-configured objects:")
print("  • client       - Neo4jClient instance")
print("  • rule_engine  - RuleEngine instance")
print("\\nCore functions:")
print("  • query(cypher)         - Execute Cypher query")
print("  • search_code(text)     - Vector search")
print("  • list_rules()          - List custom rules")
print("  • execute_rule(id)      - Execute a rule")
print("  • codebase_stats()      - Show codebase stats")
print("\\nData filtering (96% token reduction):")
print("  • summarize(results, ['field1', 'field2'])")
print("  • top_n(results, 5, 'complexity')")
print("  • count_by(results, 'severity')")
print("  • filter_by(results, severity='HIGH')")
print("  • to_table(results, ['name', 'file'])")
print("\\nState persistence:")
print("  • store('key', value) / get('key')")
print("  • cache_query('key', cypher)")
print("\\nSkill management:")
print("  • save_skill('name', code) / load_skill('name')")
print("  • list_skills() / search_skills('query')")
print("\\nExample:")
print("  results = query('MATCH (f:Function) WHERE f.complexity > 20 RETURN f')")
print("  print(to_table(top_n(results, 5, 'complexity'), ['qualifiedName', 'complexity']))")
print("=" * 60 + "\\n")
"""


def get_startup_script() -> str:
    """Get the startup script for code execution environment.

    Returns:
        Python code to execute on environment initialization
    """
    return REPOTOIRE_STARTUP_SCRIPT


def get_environment_config() -> Dict[str, Any]:
    """Get configuration for code execution environment.

    Returns:
        Dictionary with environment configuration
    """
    return {
        "python_path": [
            str(Path(__file__).parent.parent.parent),  # repotoire project root
        ],
        "env_vars": {
            "REPOTOIRE_NEO4J_URI": "bolt://localhost:7688",
            # Password loaded from .env or set by user
        },
        "startup_script": get_startup_script(),
        "description": """
Repotoire Code Execution Environment

This environment provides a Jupyter-like Python kernel pre-configured
with Repotoire imports and utilities. Write Python code to:

- Query the Neo4j knowledge graph
- Execute custom rules
- Analyze code patterns
- Process findings
- Build reusable analysis functions

All data processing happens locally in this environment, reducing
token usage and improving performance.
        """.strip()
    }


# Tool definition for MCP server
EXECUTE_CODE_TOOL = {
    "name": "execute_code",
    "description": """Execute Python code in a Repotoire-configured environment.

This environment includes:
- Pre-connected Neo4j client
- RuleEngine for custom rules
- Helper functions: query(), search_code(), execute_rule()
- All Repotoire models and utilities

Use this to write custom analysis code, process graph data locally,
and build reusable functions. Much more efficient than multiple tool calls.

Example:
    # Find high-complexity functions
    results = query(\"\"\"
        MATCH (f:Function)
        WHERE f.complexity > 20
        RETURN f.qualifiedName, f.complexity
        ORDER BY f.complexity DESC
        LIMIT 10
    \"\"\")

    # Process locally
    critical = [r for r in results if r['complexity'] > 30]
    print(f"Found {len(critical)} critical issues")
""",
    "inputSchema": {
        "type": "object",
        "properties": {
            "code": {
                "type": "string",
                "description": "Python code to execute"
            },
            "timeout": {
                "type": "number",
                "description": "Timeout in seconds (default: 30)",
                "default": 30
            }
        },
        "required": ["code"]
    }
}


# =============================================================================
# REPO-289: Secure MCP Skill Execution
# =============================================================================
# Replaces vulnerable exec() calls with sandboxed E2B execution.
# SECURITY: Never falls back to local exec() - fails secure if sandbox unavailable.
# =============================================================================


class MCPSkillRunner:
    """Secure MCP skill runner using E2B sandboxed execution.

    This class provides a complete API for executing MCP skills securely
    in isolated E2B sandboxes. It replaces the vulnerable pattern of using
    exec() to run skill code on the host.

    SECURITY REQUIREMENTS:
    - NEVER falls back to local exec() - fails secure if sandbox unavailable
    - All skill executions are logged for audit trail
    - Timeout and memory limits are enforced
    - Skill errors don't crash the host process
    - Input/output is JSON-serializable

    Usage:
        ```python
        runner = MCPSkillRunner()

        # Execute skill from code string
        result = await runner.execute_skill(
            skill_code='''
            def analyze(code: str) -> dict:
                return {"lines": len(code.split())}
            ''',
            skill_name="analyze",
            context={"code": "def foo(): pass"}
        )
        print(result.result)  # {"lines": 3}

        # Execute saved skill
        result = await runner.execute_saved_skill(
            "find_god_classes",
            threshold=15
        )

        # Get audit log
        for entry in runner.get_audit_log():
            print(f"{entry.timestamp}: {entry.skill_name} - {entry.success}")
        ```

    Configuration:
        Environment variables:
        - E2B_API_KEY: Required for sandbox execution
        - E2B_TIMEOUT_SECONDS: Skill timeout (default: 300 = 5 min)
        - E2B_MEMORY_MB: Memory limit (default: 1024 = 1GB)
    """

    def __init__(
        self,
        timeout_seconds: int = 300,
        memory_mb: int = 1024,
        enable_audit_log: bool = True,
    ):
        """Initialize the secure skill runner.

        Args:
            timeout_seconds: Maximum skill execution time (default: 5 min)
            memory_mb: Memory limit for sandbox (default: 1GB)
            enable_audit_log: Whether to log all executions for audit trail
        """
        self.config = SkillExecutorConfig(
            timeout_seconds=timeout_seconds,
            memory_mb=memory_mb,
            enable_audit_log=enable_audit_log,
        )
        self._audit_log: List[Dict[str, Any]] = []

    async def execute_skill(
        self,
        skill_code: str,
        skill_name: str,
        context: Optional[Dict[str, Any]] = None,
        timeout: Optional[int] = None,
    ) -> SkillResult:
        """Execute skill code securely in E2B sandbox.

        This is the primary method for secure skill execution. It:
        1. Creates an isolated E2B sandbox
        2. Serializes the context to JSON
        3. Generates a wrapper script
        4. Executes in the sandbox
        5. Deserializes and returns the result
        6. Logs the execution for audit trail

        Args:
            skill_code: Python code containing the skill function
            skill_name: Name of the function to call
            context: Input arguments as a dictionary (must be JSON-serializable)
            timeout: Override timeout in seconds (default: use config)

        Returns:
            SkillResult with execution result or error

        Raises:
            SkillSecurityError: If sandbox is not configured
            SkillTimeoutError: If execution exceeds timeout
            SkillExecutionError: If execution fails in sandbox

        Example:
            ```python
            result = await runner.execute_skill(
                skill_code='''
                def count_functions(code: str) -> int:
                    import ast
                    tree = ast.parse(code)
                    return len([n for n in ast.walk(tree)
                                if isinstance(n, ast.FunctionDef)])
                ''',
                skill_name="count_functions",
                context={"code": "def foo(): pass\\ndef bar(): pass"}
            )
            print(result.result)  # 2
            ```
        """
        start_time = time.time()
        skill_hash = hashlib.sha256(skill_code.encode()).hexdigest()[:16]

        logger.info(
            f"Executing skill '{skill_name}' in sandbox",
            extra={
                "skill_name": skill_name,
                "skill_hash": skill_hash,
                "context_keys": list((context or {}).keys()),
            },
        )

        try:
            async with SkillExecutor(self.config) as executor:
                result = await executor.execute_skill(
                    skill_code=skill_code,
                    skill_name=skill_name,
                    context=context,
                    timeout=timeout,
                )

                # Log to internal audit log
                duration_ms = int((time.time() - start_time) * 1000)
                self._audit_log.append({
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                    "skill_name": skill_name,
                    "skill_hash": skill_hash,
                    "success": result.success,
                    "duration_ms": duration_ms,
                    "error": result.error if not result.success else None,
                })

                return result

        except SkillSecurityError:
            # Log security failures
            self._audit_log.append({
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "skill_name": skill_name,
                "skill_hash": skill_hash,
                "success": False,
                "duration_ms": int((time.time() - start_time) * 1000),
                "error": "Sandbox not configured",
            })
            raise

    async def execute_saved_skill(
        self,
        skill_name: str,
        function_name: Optional[str] = None,
        timeout: Optional[int] = None,
        **kwargs: Any,
    ) -> SkillResult:
        """Execute a saved skill from ~/.repotoire/skills/.

        Args:
            skill_name: Name of the saved skill file (without .py extension)
            function_name: Specific function to call (default: same as skill_name)
            timeout: Override timeout in seconds
            **kwargs: Arguments to pass to the skill function

        Returns:
            SkillResult with execution result or error

        Raises:
            FileNotFoundError: If skill doesn't exist
            SkillSecurityError: If sandbox is not configured
            SkillExecutionError: If execution fails

        Example:
            ```python
            result = await runner.execute_saved_skill(
                "find_god_classes",
                threshold=15
            )
            print(result.result)
            ```
        """
        skill_file = SKILLS_DIR / f"{skill_name}.py"

        if not skill_file.exists():
            available = list_skills()
            available_str = ", ".join(available) if available else "none"
            raise FileNotFoundError(
                f"Skill '{skill_name}' not found. Available: {available_str}"
            )

        code = skill_file.read_text(encoding="utf-8")
        func_name = function_name or skill_name

        return await self.execute_skill(
            skill_code=code,
            skill_name=func_name,
            context=kwargs,
            timeout=timeout,
        )

    def execute_skill_sync(
        self,
        skill_code: str,
        skill_name: str,
        context: Optional[Dict[str, Any]] = None,
        timeout: Optional[int] = None,
    ) -> SkillResult:
        """Synchronous wrapper for execute_skill.

        Use this when you need to execute skills from synchronous code.

        Args:
            skill_code: Python code containing the skill function
            skill_name: Name of the function to call
            context: Input arguments as a dictionary
            timeout: Override timeout in seconds

        Returns:
            SkillResult with execution result or error
        """
        try:
            loop = asyncio.get_running_loop()
            # Already in async context - need to use nest_asyncio or thread
            import concurrent.futures
            with concurrent.futures.ThreadPoolExecutor() as executor:
                future = executor.submit(
                    asyncio.run,
                    self.execute_skill(skill_code, skill_name, context, timeout)
                )
                return future.result()
        except RuntimeError:
            # No running loop, create one
            return asyncio.run(
                self.execute_skill(skill_code, skill_name, context, timeout)
            )

    def get_audit_log(self) -> List[Dict[str, Any]]:
        """Get the audit log of skill executions.

        Returns:
            List of audit log entries with timestamp, skill_name, success, etc.
        """
        return list(self._audit_log)

    def clear_audit_log(self) -> int:
        """Clear the audit log.

        Returns:
            Number of entries cleared
        """
        count = len(self._audit_log)
        self._audit_log.clear()
        return count

    def export_audit_log(self, path: Optional[Path] = None) -> str:
        """Export audit log to JSON file.

        Args:
            path: Output path (default: ~/.repotoire/skill_audit.json)

        Returns:
            Path to exported file
        """
        if path is None:
            path = SKILLS_DIR.parent / "skill_audit.json"

        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(self._audit_log, indent=2), encoding="utf-8")

        return str(path)


# Global runner instance for convenience
_global_skill_runner: Optional[MCPSkillRunner] = None


def get_skill_runner() -> MCPSkillRunner:
    """Get the global MCPSkillRunner instance.

    Creates a new instance if one doesn't exist.

    Returns:
        Global MCPSkillRunner instance
    """
    global _global_skill_runner
    if _global_skill_runner is None:
        _global_skill_runner = MCPSkillRunner()
    return _global_skill_runner


async def execute_skill_secure(
    skill_code: str,
    skill_name: str,
    context: Optional[Dict[str, Any]] = None,
    timeout: Optional[int] = None,
) -> SkillResult:
    """Execute skill code securely in E2B sandbox.

    This is a convenience function that uses the global MCPSkillRunner.

    Args:
        skill_code: Python code containing the skill function
        skill_name: Name of the function to call
        context: Input arguments as a dictionary
        timeout: Override timeout in seconds

    Returns:
        SkillResult with execution result or error

    Example:
        ```python
        result = await execute_skill_secure(
            skill_code="def analyze(x): return x * 2",
            skill_name="analyze",
            context={"x": 21}
        )
        print(result.result)  # 42
        ```
    """
    runner = get_skill_runner()
    return await runner.execute_skill(skill_code, skill_name, context, timeout)

"""External node labeling for knowledge graph data quality.

This module provides utilities to correctly label external/builtin function nodes
when creating CALLS relationships. Without proper labels, nodes created for external
references (like `len`, `print`, `Path`) would have no labels, breaking label-based
Cypher queries and graph analysis.

KG-1 Fix: 32.4% of nodes were unlabeled due to MERGE creating nodes without labels.
"""

from typing import Optional

# Python built-in functions that should be labeled as BuiltinFunction
# Source: https://docs.python.org/3/library/functions.html
PYTHON_BUILTINS = frozenset({
    # Type conversions
    'int', 'float', 'complex', 'str', 'bytes', 'bytearray',
    'bool', 'list', 'tuple', 'set', 'frozenset', 'dict',

    # I/O
    'print', 'input', 'open',

    # Iteration and sequences
    'len', 'range', 'enumerate', 'zip', 'map', 'filter',
    'sorted', 'reversed', 'iter', 'next', 'slice',

    # Math and numbers
    'abs', 'round', 'pow', 'divmod', 'sum', 'min', 'max',
    'bin', 'oct', 'hex', 'ord', 'chr',

    # Object introspection
    'type', 'isinstance', 'issubclass', 'id', 'hash',
    'callable', 'dir', 'vars', 'locals', 'globals',
    'hasattr', 'getattr', 'setattr', 'delattr',

    # Object creation and manipulation
    'object', 'super', 'property', 'classmethod', 'staticmethod',
    'memoryview',

    # Aggregation
    'all', 'any',

    # String formatting
    'repr', 'ascii', 'format',

    # Compilation and execution
    'eval', 'exec', 'compile', '__import__',

    # Attribute access helpers
    'getattr', 'setattr', 'delattr', 'hasattr',

    # Debugging
    'breakpoint',

    # Help
    'help',
})

# Common standard library modules that should be labeled as ExternalFunction
# These are NOT builtins but are very commonly used
STDLIB_TOP_LEVEL_MODULES = frozenset({
    'os', 'sys', 'io', 're', 'json', 'yaml', 'csv',
    'pathlib', 'Path',  # pathlib.Path is very common
    'datetime', 'time', 'calendar',
    'collections', 'itertools', 'functools', 'operator',
    'typing', 'types',
    'logging', 'warnings',
    'unittest', 'pytest',
    'threading', 'multiprocessing', 'concurrent', 'asyncio',
    'subprocess', 'shutil', 'tempfile', 'glob',
    'pickle', 'shelve', 'sqlite3',
    'http', 'urllib', 'socket', 'email',
    'hashlib', 'hmac', 'secrets',
    'random', 'math', 'statistics', 'decimal', 'fractions',
    'copy', 'pprint', 'textwrap',
    'abc', 'contextlib', 'dataclasses', 'enum',
})


def get_external_node_label(name: str, qualified_name: Optional[str] = None) -> str:
    """Determine the appropriate label for an external/unresolved node.

    This function is used when creating CALLS relationships where the target
    doesn't exist in the codebase. Instead of creating an unlabeled node,
    we assign an appropriate label based on the target name.

    Args:
        name: Simple name of the target (e.g., "len", "print", "Path")
        qualified_name: Full qualified name if available (e.g., "pathlib.Path")

    Returns:
        Label string: "BuiltinFunction", "ExternalFunction", or "ExternalClass"

    Examples:
        >>> get_external_node_label("len")
        'BuiltinFunction'
        >>> get_external_node_label("Path", "pathlib.Path")
        'ExternalClass'
        >>> get_external_node_label("some_lib_func")
        'ExternalFunction'
    """
    # Check if it's a Python builtin
    if name in PYTHON_BUILTINS:
        return "BuiltinFunction"

    # Check if the name starts with uppercase (likely a class)
    if name and name[0].isupper():
        return "ExternalClass"

    # Check qualified name for module hints
    if qualified_name:
        # Extract module prefix if present
        parts = qualified_name.split('.')
        if len(parts) > 1:
            module = parts[0]
            if module in STDLIB_TOP_LEVEL_MODULES:
                # It's from stdlib
                if parts[-1][0].isupper():
                    return "ExternalClass"
                return "ExternalFunction"

    # Default to ExternalFunction for unresolved calls
    return "ExternalFunction"


def is_likely_external_reference(qualified_name: str) -> bool:
    """Check if a qualified name is likely an external (non-project) reference.

    External references typically:
    - Don't contain "::" (our internal qualified name separator)
    - Are short names like "len", "Path"
    - Start with known stdlib module names

    Args:
        qualified_name: The qualified name to check

    Returns:
        True if likely external, False if likely internal

    Examples:
        >>> is_likely_external_reference("len")
        True
        >>> is_likely_external_reference("pathlib.Path")
        True
        >>> is_likely_external_reference("myfile.py::MyClass.method:123")
        False
    """
    # Our internal qualified names use "::" as separator
    if "::" in qualified_name:
        return False

    # Single names without dots are likely builtins or simple external refs
    if "." not in qualified_name:
        return True

    # Check if it starts with a known stdlib module
    parts = qualified_name.split('.')
    if parts[0] in STDLIB_TOP_LEVEL_MODULES:
        return True

    return True  # Default to treating as external for safety

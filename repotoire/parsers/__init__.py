"""Code parsers for different programming languages."""

from repotoire.parsers.base import CodeParser
from repotoire.parsers.base_tree_sitter_parser import BaseTreeSitterParser
from repotoire.parsers.generic_fallback_parser import (
    EXTENSION_TO_LANGUAGE,
    GenericFallbackParser,
)
from repotoire.parsers.python_parser import PythonParser
from repotoire.parsers.tree_sitter_adapter import TreeSitterAdapter, UniversalASTNode
from repotoire.parsers.tree_sitter_python import TreeSitterPythonParser

# Optional TypeScript/JavaScript parsers (requires tree-sitter-typescript)
try:
    from repotoire.parsers.tree_sitter_typescript import (
        TreeSitterJavaScriptParser,
        TreeSitterTypeScriptParser,
    )
    _HAS_TYPESCRIPT = True
except ImportError:
    _HAS_TYPESCRIPT = False
    TreeSitterTypeScriptParser = None  # type: ignore
    TreeSitterJavaScriptParser = None  # type: ignore

# Optional Java parser (requires tree-sitter-java)
try:
    from repotoire.parsers.tree_sitter_java import TreeSitterJavaParser
    _HAS_JAVA = True
except ImportError:
    _HAS_JAVA = False
    TreeSitterJavaParser = None  # type: ignore

# Optional Go parser (requires tree-sitter-go)
try:
    from repotoire.parsers.tree_sitter_go import TreeSitterGoParser
    _HAS_GO = True
except ImportError:
    _HAS_GO = False
    TreeSitterGoParser = None  # type: ignore

__all__ = [
    "CodeParser",
    "PythonParser",
    "UniversalASTNode",
    "TreeSitterAdapter",
    "BaseTreeSitterParser",
    "TreeSitterPythonParser",
    "TreeSitterTypeScriptParser",
    "TreeSitterJavaScriptParser",
    "TreeSitterJavaParser",
    "TreeSitterGoParser",
    "GenericFallbackParser",
    "EXTENSION_TO_LANGUAGE",
]

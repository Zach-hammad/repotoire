"""Code parsers for different programming languages."""

from repotoire.parsers.base import CodeParser
from repotoire.parsers.python_parser import PythonParser
from repotoire.parsers.tree_sitter_adapter import UniversalASTNode, TreeSitterAdapter
from repotoire.parsers.base_tree_sitter_parser import BaseTreeSitterParser
from repotoire.parsers.tree_sitter_python import TreeSitterPythonParser

# Optional TypeScript/JavaScript parsers (requires tree-sitter-typescript)
try:
    from repotoire.parsers.tree_sitter_typescript import (
        TreeSitterTypeScriptParser,
        TreeSitterJavaScriptParser,
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
]

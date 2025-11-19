"""Code parsers for different programming languages."""

from falkor.parsers.base import CodeParser
from falkor.parsers.python_parser import PythonParser

__all__ = ["CodeParser", "PythonParser"]

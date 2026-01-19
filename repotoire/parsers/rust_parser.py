"""High-performance parser using Rust tree-sitter bindings.

This module provides a fast parallel parsing layer that leverages the
repotoire_fast Rust extension for tree-sitter based AST parsing.

Performance:
- Parsing: 10-100x faster than Python AST (parallel tree-sitter)
- Batch processing: All files parsed in parallel using Rayon
- Memory efficient: No Python GIL contention during parsing
"""

import os
from pathlib import Path
from typing import Dict, List, Optional, Tuple
from collections import defaultdict

from repotoire.models import (
    Entity,
    FileEntity,
    ClassEntity,
    FunctionEntity,
    Relationship,
    RelationshipType,
)
from repotoire.logging_config import get_logger

logger = get_logger(__name__)

# Lazily import repotoire_fast to avoid import errors if not built
_rf = None


def _get_repotoire_fast():
    """Lazily import repotoire_fast module."""
    global _rf
    if _rf is None:
        try:
            import repotoire_fast as rf
            _rf = rf
        except ImportError:
            logger.warning("repotoire_fast not available, Rust parsing disabled")
            return None
    return _rf


def is_rust_parser_available() -> bool:
    """Check if Rust parser is available."""
    rf = _get_repotoire_fast()
    if rf is None:
        return False
    # Check for tree-sitter functions
    return hasattr(rf, 'parse_files_parallel')


def get_supported_languages() -> List[str]:
    """Get list of languages supported by the Rust parser."""
    rf = _get_repotoire_fast()
    if rf is None:
        return []
    return rf.get_supported_languages()


def parse_files_parallel(
    files: List[Tuple[str, str, str]],
    repo_path: str,
) -> Dict[str, Tuple[List[Entity], List[Relationship]]]:
    """Parse multiple files in parallel using Rust tree-sitter.

    Args:
        files: List of (file_path, source_code, language) tuples
        repo_path: Repository root path for relative path calculation

    Returns:
        Dict mapping file_path -> (entities, relationships)
    """
    rf = _get_repotoire_fast()
    if rf is None:
        return {}

    if not files:
        return {}

    # Call Rust parallel parser
    logger.debug(f"Parsing {len(files)} files in parallel with tree-sitter")
    parsed_results = rf.parse_files_parallel(files)

    # Convert to Entity/Relationship format
    results = {}
    for parsed in parsed_results:
        entities, relationships = _convert_parsed_file(parsed, repo_path)
        results[parsed.path] = (entities, relationships)

    logger.debug(f"Parsed {len(results)} files successfully")
    return results


def parse_files_parallel_auto(
    files: List[Tuple[str, str]],
    repo_path: str,
) -> Dict[str, Tuple[List[Entity], List[Relationship]]]:
    """Parse multiple files with auto language detection.

    Args:
        files: List of (file_path, source_code) tuples
        repo_path: Repository root path for relative path calculation

    Returns:
        Dict mapping file_path -> (entities, relationships)
    """
    rf = _get_repotoire_fast()
    if rf is None:
        return {}

    if not files:
        return {}

    # Call Rust parallel parser with auto detection
    logger.debug(f"Parsing {len(files)} files in parallel with auto language detection")
    parsed_results = rf.parse_files_parallel_auto(files)

    # Convert to Entity/Relationship format
    results = {}
    for parsed in parsed_results:
        if parsed.parse_error:
            logger.debug(f"Parse error for {parsed.path}: {parsed.parse_error}")
            continue
        entities, relationships = _convert_parsed_file(parsed, repo_path)
        results[parsed.path] = (entities, relationships)

    logger.debug(f"Parsed {len(results)} files successfully")
    return results


def _convert_parsed_file(
    parsed,
    repo_path: str,
) -> Tuple[List[Entity], List[Relationship]]:
    """Convert a PyParsedFile to Entity/Relationship lists.

    Args:
        parsed: PyParsedFile from Rust parser
        repo_path: Repository root path

    Returns:
        Tuple of (entities, relationships)
    """
    entities: List[Entity] = []
    relationships: List[Relationship] = []

    file_path = parsed.path
    rel_path = _get_relative_path(file_path, repo_path)

    # Create File entity
    file_entity = FileEntity(
        name=Path(file_path).name,
        qualified_name=rel_path,
        file_path=rel_path,
        line_start=1,
        line_end=_get_max_line(parsed),
    )
    entities.append(file_entity)

    # Track class qualified names for method linking
    class_qn_map: Dict[str, str] = {}  # class_name -> qualified_name

    # Convert classes
    for cls in parsed.classes:
        class_qn = f"{rel_path}::{cls.name}:{cls.start_line}"
        class_qn_map[cls.name] = class_qn

        # Determine if abstract class
        is_abstract = any(
            base in ("ABC", "abc.ABC") for base in (cls.base_classes or [])
        )

        # Check for dataclass decorator
        is_dataclass = "dataclass" in (cls.decorators or [])

        # Check if exception class
        is_exception = any(
            "Exception" in base or "Error" in base
            for base in (cls.base_classes or [])
        )

        class_entity = ClassEntity(
            name=cls.name,
            qualified_name=class_qn,
            file_path=rel_path,
            line_start=cls.start_line,
            line_end=cls.end_line,
            docstring=cls.docstring,
            is_abstract=is_abstract,
            complexity=0,  # Will be calculated separately if needed
            decorators=cls.decorators or [],
            is_dataclass=is_dataclass,
            is_exception=is_exception,
            nesting_level=0,  # TODO: track nesting
        )
        entities.append(class_entity)

        # Create CONTAINS relationship (File -> Class)
        relationships.append(Relationship(
            source_id=file_entity.qualified_name,
            target_id=class_qn,
            rel_type=RelationshipType.CONTAINS,
        ))

        # Create INHERITS relationships for base classes
        for base in (cls.base_classes or []):
            relationships.append(Relationship(
                source_id=class_qn,
                target_id=base,  # Will be resolved later
                rel_type=RelationshipType.INHERITS,
            ))

    # Convert functions
    for func in parsed.functions:
        # Determine if method or top-level function
        if func.is_method and func.parent_class:
            parent_qn = class_qn_map.get(func.parent_class)
            if parent_qn:
                func_qn = f"{parent_qn}.{func.name}:{func.start_line}"
            else:
                func_qn = f"{rel_path}::{func.parent_class}.{func.name}:{func.start_line}"
        else:
            func_qn = f"{rel_path}::{func.name}:{func.start_line}"

        # Check decorator types
        decorators = func.decorators or []
        is_static = "staticmethod" in decorators
        is_classmethod = "classmethod" in decorators
        is_property = "property" in decorators

        func_entity = FunctionEntity(
            name=func.name,
            qualified_name=func_qn,
            file_path=rel_path,
            line_start=func.start_line,
            line_end=func.end_line,
            parameters=func.parameters or [],
            parameter_types={},  # Not extracted by tree-sitter yet
            return_type=func.return_type,
            complexity=0,  # Will be calculated separately if needed
            is_async=func.is_async,
            decorators=decorators,
            is_method=func.is_method,
            is_static=is_static,
            is_classmethod=is_classmethod,
            is_property=is_property,
            has_return=False,  # Not extracted by tree-sitter yet
            has_yield=False,  # Not extracted by tree-sitter yet
            docstring=func.docstring,
        )
        entities.append(func_entity)

        # Create CONTAINS relationship
        if func.is_method and func.parent_class:
            parent_qn = class_qn_map.get(func.parent_class)
            if parent_qn:
                relationships.append(Relationship(
                    source_id=parent_qn,
                    target_id=func_qn,
                    rel_type=RelationshipType.CONTAINS,
                ))
        else:
            relationships.append(Relationship(
                source_id=file_entity.qualified_name,
                target_id=func_qn,
                rel_type=RelationshipType.CONTAINS,
            ))

    # Convert imports to relationships
    for imp in parsed.imports:
        # Create IMPORTS relationship
        for name in imp.names:
            if imp.is_from_import:
                target = f"{imp.module}.{name}" if imp.module else name
            else:
                target = name
            relationships.append(Relationship(
                source_id=file_entity.qualified_name,
                target_id=target,
                rel_type=RelationshipType.IMPORTS,
            ))

    # Build a mapping from simple function names to qualified names
    # This helps resolve calls like "self.method()" to actual function entities
    func_qn_by_name: Dict[str, List[str]] = defaultdict(list)
    for ent in entities:
        if isinstance(ent, FunctionEntity):
            func_qn_by_name[ent.name].append(ent.qualified_name)

    # Convert calls to relationships (if calls are available)
    if hasattr(parsed, 'calls'):
        for call in parsed.calls:
            # Find the caller function's qualified name
            # The caller_qualified_name from Rust is in module.ClassName.method format
            # We need to map it to our qualified_name format
            caller_qn = _resolve_caller_qn(
                call.caller_qualified_name,
                rel_path,
                class_qn_map,
                func_qn_by_name,
            )

            if caller_qn:
                # Determine the target (callee)
                # For method calls like self.method(), try to resolve within the class
                callee = call.callee

                # Create CALLS relationship
                relationships.append(Relationship(
                    source_id=caller_qn,
                    target_id=callee,
                    rel_type=RelationshipType.CALLS,
                    properties={
                        'line': call.line,
                        'is_method_call': call.is_method_call,
                        'receiver': call.receiver,
                    },
                ))

    return entities, relationships


def _resolve_caller_qn(
    rust_caller_qn: str,
    rel_path: str,
    class_qn_map: Dict[str, str],
    func_qn_by_name: Dict[str, List[str]],
) -> Optional[str]:
    """Resolve a Rust caller qualified name to our qualified name format.

    Args:
        rust_caller_qn: Caller qualified name from Rust (e.g., "module.ClassName.method")
        rel_path: Relative file path
        class_qn_map: Mapping of class names to their qualified names
        func_qn_by_name: Mapping of function names to their qualified names

    Returns:
        The resolved qualified name or None if not found
    """
    parts = rust_caller_qn.split('.')

    if len(parts) >= 3:
        # Method: module.ClassName.method
        class_name = parts[-2]
        method_name = parts[-1]
        if class_name in class_qn_map:
            # Find the method in our function entities
            for qn in func_qn_by_name.get(method_name, []):
                if class_name in qn:
                    return qn

    if len(parts) >= 2:
        # Top-level function: module.function
        func_name = parts[-1]
        candidates = func_qn_by_name.get(func_name, [])
        if candidates:
            # Prefer functions in this file
            for qn in candidates:
                if rel_path in qn:
                    return qn
            return candidates[0]

    return None


def _get_relative_path(file_path: str, repo_path: str) -> str:
    """Convert absolute path to relative path."""
    try:
        return str(Path(file_path).relative_to(Path(repo_path)))
    except ValueError:
        return file_path


def _get_max_line(parsed) -> int:
    """Get maximum line number from parsed file."""
    max_line = 1
    for cls in parsed.classes:
        max_line = max(max_line, cls.end_line)
    for func in parsed.functions:
        max_line = max(max_line, func.end_line)
    return max_line


def batch_read_files(file_paths: List[str]) -> List[Tuple[str, str]]:
    """Read multiple files and return their contents.

    Args:
        file_paths: List of file paths to read

    Returns:
        List of (file_path, content) tuples for files that were read successfully
    """
    results = []
    for path in file_paths:
        try:
            with open(path, 'r', encoding='utf-8', errors='replace') as f:
                content = f.read()
            results.append((path, content))
        except (IOError, OSError) as e:
            logger.debug(f"Failed to read {path}: {e}")
    return results


class RustParallelParser:
    """Adapter class for using Rust parser in the ingestion pipeline.

    This class provides a compatible interface with the existing parser
    infrastructure while leveraging Rust for parallel parsing.
    """

    def __init__(self, repo_path: str):
        """Initialize the Rust parser adapter.

        Args:
            repo_path: Repository root path
        """
        self.repo_path = str(Path(repo_path).resolve())
        self._supported_languages = get_supported_languages()
        self._extension_map = {
            '.py': 'python',
            '.ts': 'typescript',
            '.tsx': 'typescript',
            '.js': 'javascript',
            '.jsx': 'javascript',
            '.java': 'java',
            '.go': 'go',
            '.rs': 'rust',
        }

    def supports_language(self, extension: str) -> bool:
        """Check if a file extension is supported."""
        lang = self._extension_map.get(extension.lower())
        return lang in self._supported_languages

    def parse_batch(
        self,
        files: List[Tuple[str, str]],
    ) -> Dict[str, Tuple[List[Entity], List[Relationship]]]:
        """Parse a batch of files in parallel.

        Args:
            files: List of (file_path, content) tuples

        Returns:
            Dict mapping file_path -> (entities, relationships)
        """
        # Add language detection
        files_with_lang = []
        for path, content in files:
            ext = Path(path).suffix.lower()
            lang = self._extension_map.get(ext)
            if lang:
                files_with_lang.append((path, content, lang))

        if not files_with_lang:
            return {}

        return parse_files_parallel(files_with_lang, self.repo_path)

    def process_files(
        self,
        file_paths: List[str],
    ) -> Dict[str, Tuple[List[Entity], List[Relationship]]]:
        """Read and parse multiple files in parallel.

        Args:
            file_paths: List of file paths to process

        Returns:
            Dict mapping file_path -> (entities, relationships)
        """
        # Read files
        files = batch_read_files(file_paths)

        # Parse in parallel
        return self.parse_batch(files)

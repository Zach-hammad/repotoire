"""Generic fallback parser for unsupported programming languages.

This module provides basic code entity extraction for languages without
dedicated parsers. It uses a two-tier approach:

1. **Tree-sitter based**: If a tree-sitter grammar is available for the language,
   uses generic tree-sitter queries to extract entities.

2. **Heuristic based**: Falls back to regex-based pattern matching for common
   language constructs (functions, classes, imports).

This ensures every file can be tracked in the knowledge graph even if
full semantic parsing isn't available.
"""

import re
from pathlib import Path
from typing import Any, Dict, List

from repotoire.logging_config import get_logger
from repotoire.models import (
    ClassEntity,
    Entity,
    FileEntity,
    FunctionEntity,
    Relationship,
    RelationshipType,
)
from repotoire.parsers.base import CodeParser

logger = get_logger(__name__)


# Common function definition patterns across languages
# Pattern: <keyword> <name>( or <name> = function
FUNCTION_PATTERNS = {
    # C-family: void func(), int func(), etc.
    "c_style": re.compile(
        r"^\s*(?:(?:static|inline|virtual|override|public|private|protected|async|export|default)\s+)*"
        r"(?:[\w:<>,\s\*&]+\s+)?"  # Return type
        r"(\w+)\s*\([^)]*\)\s*(?:const|override|noexcept|throws[\w\s,]*)?(?:\s*->.*?)?\s*[{;]",
        re.MULTILINE
    ),
    # Python/Ruby style: def name
    "def_style": re.compile(
        r"^\s*(?:async\s+)?def\s+(\w+)\s*\(",
        re.MULTILINE
    ),
    # Rust: fn name / pub fn name
    "fn_style": re.compile(
        r"^\s*(?:pub(?:\s*\([^)]*\))?\s+)?(?:async\s+)?(?:unsafe\s+)?(?:const\s+)?fn\s+(\w+)\s*(?:<[^>]*>)?\s*\(",
        re.MULTILINE
    ),
    # Ruby: def name
    "ruby_def": re.compile(
        r"^\s*def\s+(\w+[?!=]?)\s*(?:\(|$)",
        re.MULTILINE
    ),
    # PHP: function name
    "php_function": re.compile(
        r"^\s*(?:public|private|protected|static|final|abstract)?\s*function\s+(\w+)\s*\(",
        re.MULTILINE
    ),
    # Kotlin: fun name
    "kotlin_fun": re.compile(
        r"^\s*(?:(?:public|private|protected|internal|override|suspend|inline|infix|operator)\s+)*fun\s+(?:<[^>]*>\s*)?(\w+)\s*\(",
        re.MULTILINE
    ),
    # Swift: func name
    "swift_func": re.compile(
        r"^\s*(?:(?:public|private|fileprivate|internal|open|static|class|final|override|mutating|async)\s+)*func\s+(\w+)\s*(?:<[^>]*>)?\s*\(",
        re.MULTILINE
    ),
    # Scala: def name
    "scala_def": re.compile(
        r"^\s*(?:(?:override|private|protected|implicit|final|lazy)\s+)*def\s+(\w+)\s*(?:\[[^\]]*\])?\s*(?:\(|:)",
        re.MULTILINE
    ),
    # Lua: function name / local function name
    "lua_function": re.compile(
        r"^\s*(?:local\s+)?function\s+(?:[\w.:]+[.:])?(\w+)\s*\(",
        re.MULTILINE
    ),
    # Perl/Ruby: sub name
    "sub_style": re.compile(
        r"^\s*sub\s+(\w+)\s*(?:\([^)]*\))?\s*[{]",
        re.MULTILINE
    ),
    # Elixir: def name / defp name
    "elixir_def": re.compile(
        r"^\s*(?:defp?|defmacrop?)\s+(\w+[?!]?)\s*(?:\(|,|\s+do)",
        re.MULTILINE
    ),
    # Haskell: name :: type then name args =
    "haskell_func": re.compile(
        r"^(\w+)\s+::\s+[^\n]+\n\1\s+[\w\s]+\s*=",
        re.MULTILINE
    ),
    # OCaml/F#: let name / let rec name
    "let_style": re.compile(
        r"^\s*let\s+(?:rec\s+)?(\w+)\s*(?:[\w\s:]+)?\s*=",
        re.MULTILINE
    ),
}

# Common class/type definition patterns
CLASS_PATTERNS = {
    # C++/Java/C#/Kotlin: class Name
    "class_keyword": re.compile(
        r"^\s*(?:(?:public|private|protected|abstract|final|sealed|partial|static|internal|data|open|inner)\s+)*"
        r"class\s+(\w+)\s*(?:<[^>]*>)?(?:\s*(?:extends|implements|:|where)[^{]*)?[{]?",
        re.MULTILINE
    ),
    # Struct: struct Name
    "struct_keyword": re.compile(
        r"^\s*(?:(?:pub(?:\s*\([^)]*\))?|public|private|internal)\s+)?struct\s+(\w+)\s*(?:<[^>]*>)?",
        re.MULTILINE
    ),
    # Interface: interface Name
    "interface_keyword": re.compile(
        r"^\s*(?:(?:public|private|protected|sealed|partial|internal)\s+)*interface\s+(\w+)\s*(?:<[^>]*>)?",
        re.MULTILINE
    ),
    # Trait: trait Name (Rust, Scala, PHP)
    "trait_keyword": re.compile(
        r"^\s*(?:(?:pub(?:\s*\([^)]*\))?|sealed)\s+)?trait\s+(\w+)\s*(?:<[^>]*>)?",
        re.MULTILINE
    ),
    # Enum: enum Name
    "enum_keyword": re.compile(
        r"^\s*(?:(?:pub(?:\s*\([^)]*\))?|public|private|internal)\s+)?enum\s+(?:class\s+)?(\w+)\s*(?:<[^>]*>)?",
        re.MULTILINE
    ),
    # Ruby: class Name / module Name
    "ruby_class": re.compile(
        r"^\s*(?:class|module)\s+(\w+(?:::\w+)*)\s*(?:<\s*[\w:]+)?",
        re.MULTILINE
    ),
    # Swift: class/struct/protocol/extension
    "swift_type": re.compile(
        r"^\s*(?:(?:public|private|fileprivate|internal|open|final)\s+)*"
        r"(?:class|struct|protocol|actor)\s+(\w+)\s*(?:<[^>]*>)?",
        re.MULTILINE
    ),
    # Kotlin: object Name
    "kotlin_object": re.compile(
        r"^\s*(?:(?:private|internal|public)\s+)?object\s+(\w+)",
        re.MULTILINE
    ),
    # Elixir: defmodule Name
    "elixir_module": re.compile(
        r"^\s*defmodule\s+([\w.]+)\s+do",
        re.MULTILINE
    ),
    # Haskell: data Name / newtype Name / type Name
    "haskell_type": re.compile(
        r"^\s*(?:data|newtype|type)\s+(\w+)\s*(?:[\w\s]+)?\s*=",
        re.MULTILINE
    ),
}

# Common import patterns
IMPORT_PATTERNS = {
    # Python/Elixir: import x / from x import y
    "python_import": re.compile(
        r"^\s*(?:from\s+([\w.]+)\s+)?import\s+([\w.,\s*]+)",
        re.MULTILINE
    ),
    # C/C++: #include
    "c_include": re.compile(
        r'^\s*#\s*include\s*[<"]([\w./]+)[>"]',
        re.MULTILINE
    ),
    # Java/Kotlin/Scala: import x.y.z
    "java_import": re.compile(
        r"^\s*import\s+(?:static\s+)?([\w.*]+)",
        re.MULTILINE
    ),
    # Rust: use x::y
    "rust_use": re.compile(
        r"^\s*(?:pub\s+)?use\s+([\w:*]+(?:::\{[^}]+\})?)",
        re.MULTILINE
    ),
    # Go: import "x" or import (...)
    "go_import": re.compile(
        r'^\s*import\s+(?:\w+\s+)?"([\w./]+)"',
        re.MULTILINE
    ),
    # Ruby: require / require_relative
    "ruby_require": re.compile(
        r'^\s*require(?:_relative)?\s+[\'"]([^\'"]+)[\'"]',
        re.MULTILINE
    ),
    # PHP: use / require / include
    "php_use": re.compile(
        r"^\s*(?:use|require(?:_once)?|include(?:_once)?)\s+([\w\\\\]+)",
        re.MULTILINE
    ),
    # Swift: import Name
    "swift_import": re.compile(
        r"^\s*import\s+(\w+)",
        re.MULTILINE
    ),
    # Haskell: import Module
    "haskell_import": re.compile(
        r"^\s*import\s+(?:qualified\s+)?([\w.]+)",
        re.MULTILINE
    ),
    # OCaml: open Module
    "ocaml_open": re.compile(
        r"^\s*open\s+(\w+)",
        re.MULTILINE
    ),
}

# Language-specific settings for better heuristics
LANGUAGE_HINTS: Dict[str, Dict[str, Any]] = {
    "c": {
        "function_patterns": ["c_style"],
        "class_patterns": ["struct_keyword", "enum_keyword"],
        "import_patterns": ["c_include"],
        "comment_prefix": "//",
        "block_comment": ("/*", "*/"),
    },
    "cpp": {
        "function_patterns": ["c_style"],
        "class_patterns": ["class_keyword", "struct_keyword", "enum_keyword"],
        "import_patterns": ["c_include"],
        "comment_prefix": "//",
        "block_comment": ("/*", "*/"),
    },
    "rust": {
        "function_patterns": ["fn_style"],
        "class_patterns": ["struct_keyword", "trait_keyword", "enum_keyword"],
        "import_patterns": ["rust_use"],
        "comment_prefix": "//",
        "block_comment": ("/*", "*/"),
    },
    "ruby": {
        "function_patterns": ["ruby_def", "def_style"],
        "class_patterns": ["ruby_class"],
        "import_patterns": ["ruby_require"],
        "comment_prefix": "#",
    },
    "php": {
        "function_patterns": ["php_function"],
        "class_patterns": ["class_keyword", "interface_keyword", "trait_keyword"],
        "import_patterns": ["php_use"],
        "comment_prefix": "//",
        "block_comment": ("/*", "*/"),
    },
    "kotlin": {
        "function_patterns": ["kotlin_fun"],
        "class_patterns": ["class_keyword", "interface_keyword", "kotlin_object"],
        "import_patterns": ["java_import"],
        "comment_prefix": "//",
        "block_comment": ("/*", "*/"),
    },
    "swift": {
        "function_patterns": ["swift_func"],
        "class_patterns": ["swift_type"],
        "import_patterns": ["swift_import"],
        "comment_prefix": "//",
        "block_comment": ("/*", "*/"),
    },
    "scala": {
        "function_patterns": ["scala_def"],
        "class_patterns": ["class_keyword", "trait_keyword", "kotlin_object"],
        "import_patterns": ["java_import"],
        "comment_prefix": "//",
        "block_comment": ("/*", "*/"),
    },
    "elixir": {
        "function_patterns": ["elixir_def"],
        "class_patterns": ["elixir_module"],
        "import_patterns": ["python_import"],
        "comment_prefix": "#",
    },
    "haskell": {
        "function_patterns": ["haskell_func", "let_style"],
        "class_patterns": ["haskell_type"],
        "import_patterns": ["haskell_import"],
        "comment_prefix": "--",
        "block_comment": ("{-", "-}"),
    },
    "ocaml": {
        "function_patterns": ["let_style"],
        "class_patterns": ["haskell_type"],
        "import_patterns": ["ocaml_open"],
        "comment_prefix": None,
        "block_comment": ("(*", "*)"),
    },
    "lua": {
        "function_patterns": ["lua_function"],
        "class_patterns": [],
        "import_patterns": ["ruby_require"],
        "comment_prefix": "--",
        "block_comment": ("--[[", "]]"),
    },
    "perl": {
        "function_patterns": ["sub_style"],
        "class_patterns": [],
        "import_patterns": ["ruby_require"],
        "comment_prefix": "#",
    },
    "csharp": {
        "function_patterns": ["c_style"],
        "class_patterns": ["class_keyword", "struct_keyword", "interface_keyword", "enum_keyword"],
        "import_patterns": ["java_import"],  # using is similar
        "comment_prefix": "//",
        "block_comment": ("/*", "*/"),
    },
}

# File extension to language mapping
EXTENSION_TO_LANGUAGE: Dict[str, str] = {
    ".c": "c",
    ".h": "c",
    ".cpp": "cpp",
    ".cc": "cpp",
    ".cxx": "cpp",
    ".hpp": "cpp",
    ".hxx": "cpp",
    ".rs": "rust",
    ".rb": "ruby",
    ".php": "php",
    ".kt": "kotlin",
    ".kts": "kotlin",
    ".swift": "swift",
    ".scala": "scala",
    ".sc": "scala",
    ".ex": "elixir",
    ".exs": "elixir",
    ".hs": "haskell",
    ".lhs": "haskell",
    ".ml": "ocaml",
    ".mli": "ocaml",
    ".lua": "lua",
    ".pl": "perl",
    ".pm": "perl",
    ".cs": "csharp",
    ".r": "r",
    ".R": "r",
    ".jl": "julia",
    ".clj": "clojure",
    ".cljs": "clojure",
    ".dart": "dart",
    ".groovy": "groovy",
    ".gvy": "groovy",
    ".v": "v",
    ".zig": "zig",
    ".nim": "nim",
    ".cr": "crystal",
    ".d": "d",
    ".pas": "pascal",
    ".pp": "pascal",
    ".f90": "fortran",
    ".f95": "fortran",
    ".f03": "fortran",
    ".ada": "ada",
    ".adb": "ada",
    ".ads": "ada",
    ".elm": "elm",
    ".purs": "purescript",
    ".fs": "fsharp",
    ".fsx": "fsharp",
    ".erl": "erlang",
    ".hrl": "erlang",
    ".coffee": "coffeescript",
    ".vue": "vue",
    ".svelte": "svelte",
}


class GenericFallbackParser(CodeParser):
    """Generic parser for languages without dedicated parsers.

    Provides basic entity extraction using regex patterns for common
    language constructs. Ensures every file is tracked in the knowledge
    graph even without full semantic parsing.

    Features:
    - Extracts File entities for all supported files
    - Detects functions using common patterns (def, fn, func, function, etc.)
    - Detects classes using common patterns (class, struct, trait, etc.)
    - Tracks import relationships using common patterns
    - Line count and basic metrics

    Example:
        >>> parser = GenericFallbackParser()
        >>> tree = parser.parse("example.rs")
        >>> entities = parser.extract_entities(tree, "example.rs")
        >>> len(entities) > 0  # At minimum, File entity
        True
    """

    def __init__(self):
        """Initialize the generic fallback parser."""
        self._tree_sitter_available: Dict[str, bool] = {}
        self._tree_sitter_parsers: Dict[str, Any] = {}

    def _detect_language_from_extension(self, file_path: str) -> str:
        """Detect language from file extension.

        Args:
            file_path: Path to the file

        Returns:
            Language identifier or 'unknown'
        """
        ext = Path(file_path).suffix.lower()
        return EXTENSION_TO_LANGUAGE.get(ext, "unknown")

    def _get_language_hints(self, language: str) -> Dict[str, Any]:
        """Get language-specific pattern hints.

        Args:
            language: Language identifier

        Returns:
            Dict of pattern hints for the language
        """
        return LANGUAGE_HINTS.get(language, {
            "function_patterns": list(FUNCTION_PATTERNS.keys()),
            "class_patterns": list(CLASS_PATTERNS.keys()),
            "import_patterns": list(IMPORT_PATTERNS.keys()),
            "comment_prefix": "//",
        })

    def _strip_comments(self, content: str, language: str) -> str:
        """Strip comments from source code for cleaner pattern matching.

        Args:
            content: Source code content
            language: Language identifier

        Returns:
            Content with comments stripped
        """
        hints = self._get_language_hints(language)

        # Strip block comments first
        block_comment = hints.get("block_comment")
        if block_comment:
            start, end = block_comment
            # Escape regex special chars
            start_esc = re.escape(start)
            end_esc = re.escape(end)
            content = re.sub(f"{start_esc}.*?{end_esc}", "", content, flags=re.DOTALL)

        # Strip line comments
        comment_prefix = hints.get("comment_prefix")
        if comment_prefix:
            prefix_esc = re.escape(comment_prefix)
            # Don't strip if inside a string (simplified check)
            content = re.sub(f'^([^"\']*)({prefix_esc}.*)$', r'\1', content, flags=re.MULTILINE)

        return content

    def parse(self, file_path: str) -> Dict[str, Any]:
        """Parse a source file into a simple representation.

        Args:
            file_path: Path to the source file

        Returns:
            Dict containing file content and metadata
        """
        path = Path(file_path)

        try:
            with open(path, "r", encoding="utf-8", errors="replace") as f:
                content = f.read()
        except Exception as e:
            logger.warning(f"Failed to read {file_path}: {e}")
            content = ""

        language = self._detect_language_from_extension(file_path)

        return {
            "content": content,
            "language": language,
            "file_path": str(path),
            "line_count": content.count("\n") + 1 if content else 0,
        }

    def extract_entities(self, ast: Dict[str, Any], file_path: str) -> List[Entity]:
        """Extract code entities from parsed content.

        Args:
            ast: Parsed file representation
            file_path: Path to the source file

        Returns:
            List of extracted entities
        """
        entities: List[Entity] = []
        content = ast.get("content", "")
        language = ast.get("language", "unknown")
        line_count = ast.get("line_count", 0)

        # Always create a File entity
        file_entity = FileEntity(
            name=Path(file_path).name,
            qualified_name=file_path,
            file_path=file_path,
            line_start=1,
            line_end=line_count,
            language=language,
            loc=line_count,
            metadata={"parser": "generic_fallback"},
        )
        entities.append(file_entity)

        if not content:
            return entities

        # Get language-specific hints
        hints = self._get_language_hints(language)

        # Strip comments for cleaner matching
        clean_content = self._strip_comments(content, language)

        # Extract functions
        function_patterns = hints.get("function_patterns", list(FUNCTION_PATTERNS.keys()))
        functions_found = self._extract_functions(clean_content, file_path, function_patterns)
        entities.extend(functions_found)

        # Extract classes
        class_patterns = hints.get("class_patterns", list(CLASS_PATTERNS.keys()))
        classes_found = self._extract_classes(clean_content, file_path, class_patterns)
        entities.extend(classes_found)

        logger.debug(
            f"Generic parser extracted {len(functions_found)} functions, "
            f"{len(classes_found)} classes from {file_path}"
        )

        return entities

    def _extract_functions(
        self,
        content: str,
        file_path: str,
        pattern_names: List[str]
    ) -> List[FunctionEntity]:
        """Extract function entities using regex patterns.

        Args:
            content: Source code content (comments stripped)
            file_path: Path to the source file
            pattern_names: List of pattern names to use

        Returns:
            List of FunctionEntity objects
        """
        functions = []
        seen_names: set = set()

        # Build line number lookup
        lines = content.split("\n")
        line_starts = [0]
        for line in lines:
            line_starts.append(line_starts[-1] + len(line) + 1)

        def get_line_number(pos: int) -> int:
            """Get 1-based line number from character position."""
            for i, start in enumerate(line_starts):
                if start > pos:
                    return i
            return len(lines)

        for pattern_name in pattern_names:
            pattern = FUNCTION_PATTERNS.get(pattern_name)
            if not pattern:
                continue

            for match in pattern.finditer(content):
                func_name = match.group(1)

                # Skip if already found (avoid duplicates from multiple patterns)
                if func_name in seen_names:
                    continue
                seen_names.add(func_name)

                # Skip common false positives
                if func_name in ("if", "for", "while", "switch", "catch", "return", "new", "delete"):
                    continue

                line_num = get_line_number(match.start())

                functions.append(FunctionEntity(
                    name=func_name,
                    qualified_name=f"{file_path}::{func_name}",
                    file_path=file_path,
                    line_start=line_num,
                    line_end=line_num,  # Can't reliably determine end
                    docstring=None,
                    complexity=1,  # Unknown, default to 1
                    is_async=False,
                    decorators=[],
                    is_method=False,
                    is_static=False,
                    is_classmethod=False,
                    is_property=False,
                    has_return=False,
                    has_yield=False,
                    metadata={"parser": "generic_fallback", "pattern": pattern_name},
                ))

        return functions

    def _extract_classes(
        self,
        content: str,
        file_path: str,
        pattern_names: List[str]
    ) -> List[ClassEntity]:
        """Extract class entities using regex patterns.

        Args:
            content: Source code content (comments stripped)
            file_path: Path to the source file
            pattern_names: List of pattern names to use

        Returns:
            List of ClassEntity objects
        """
        classes = []
        seen_names: set = set()

        # Build line number lookup
        lines = content.split("\n")
        line_starts = [0]
        for line in lines:
            line_starts.append(line_starts[-1] + len(line) + 1)

        def get_line_number(pos: int) -> int:
            """Get 1-based line number from character position."""
            for i, start in enumerate(line_starts):
                if start > pos:
                    return i
            return len(lines)

        for pattern_name in pattern_names:
            pattern = CLASS_PATTERNS.get(pattern_name)
            if not pattern:
                continue

            for match in pattern.finditer(content):
                class_name = match.group(1)

                # Skip if already found
                if class_name in seen_names:
                    continue
                seen_names.add(class_name)

                line_num = get_line_number(match.start())

                classes.append(ClassEntity(
                    name=class_name,
                    qualified_name=f"{file_path}::{class_name}",
                    file_path=file_path,
                    line_start=line_num,
                    line_end=line_num,
                    docstring=None,
                    decorators=[],
                    is_dataclass=False,
                    is_exception=False,
                    nesting_level=0,
                    metadata={"parser": "generic_fallback", "pattern": pattern_name},
                ))

        return classes

    def extract_relationships(
        self,
        ast: Dict[str, Any],
        file_path: str,
        entities: List[Entity]
    ) -> List[Relationship]:
        """Extract relationships from parsed content.

        Args:
            ast: Parsed file representation
            file_path: Path to the source file
            entities: Previously extracted entities

        Returns:
            List of relationships (imports, etc.)
        """
        relationships = []
        content = ast.get("content", "")
        language = ast.get("language", "unknown")

        if not content:
            return relationships

        # Get language-specific hints
        hints = self._get_language_hints(language)
        import_patterns = hints.get("import_patterns", list(IMPORT_PATTERNS.keys()))

        # Extract import relationships
        imports = self._extract_imports(content, file_path, import_patterns)
        relationships.extend(imports)

        return relationships

    def _extract_imports(
        self,
        content: str,
        file_path: str,
        pattern_names: List[str]
    ) -> List[Relationship]:
        """Extract import relationships using regex patterns.

        Args:
            content: Source code content
            file_path: Path to the source file
            pattern_names: List of pattern names to use

        Returns:
            List of import Relationship objects
        """
        relationships = []
        seen_imports: set = set()

        for pattern_name in pattern_names:
            pattern = IMPORT_PATTERNS.get(pattern_name)
            if not pattern:
                continue

            for match in pattern.finditer(content):
                # Get the imported module/package
                groups = match.groups()
                import_target = None

                if pattern_name == "python_import":
                    # from x import y -> x.y, or import x -> x
                    from_module = groups[0] if groups[0] else ""
                    imports = groups[1] if len(groups) > 1 and groups[1] else ""

                    if from_module:
                        import_target = from_module
                    else:
                        # Clean up "import x, y, z" -> take first
                        import_target = imports.split(",")[0].strip()
                else:
                    # Most patterns capture the module in group 1
                    import_target = groups[0] if groups else None

                if not import_target or import_target in seen_imports:
                    continue
                seen_imports.add(import_target)

                relationships.append(Relationship(
                    source_id=file_path,
                    target_id=import_target,
                    rel_type=RelationshipType.IMPORTS,
                    properties={
                        "parser": "generic_fallback",
                        "pattern": pattern_name,
                    },
                ))

        return relationships

    @staticmethod
    def supported_extensions() -> List[str]:
        """Get list of file extensions this parser supports.

        Returns:
            List of supported file extensions
        """
        return list(EXTENSION_TO_LANGUAGE.keys())

    @staticmethod
    def supported_languages() -> List[str]:
        """Get list of languages this parser supports.

        Returns:
            List of unique supported languages
        """
        return list(set(EXTENSION_TO_LANGUAGE.values()))

# Adding New Language Support to Repotoire

This guide shows how to add support for a new programming language using the tree-sitter universal AST adapter pattern.

## Why This Approach?

**Problem**: Traditional approach requires ~300 lines of boilerplate per language, learning each parser's unique API, and no code reuse.

**Solution**: The `UniversalASTNode` abstraction provides a uniform API across all languages. You get `find_all("function_definition")` that works identically for Python, TypeScript, Java, etc.

## Quick Start: 5 Steps to Add a Language

### Step 1: Install tree-sitter Language Package

```bash
# For TypeScript
pip install tree-sitter-typescript

# For Java
pip install tree-sitter-java

# For Go
pip install tree-sitter-go
```

Add the dependency to `pyproject.toml`:

```toml
[project.optional-dependencies]
all-languages = [
    "tree-sitter>=0.20.0",
    "tree-sitter-python>=0.20.0",
    "tree-sitter-typescript>=0.20.0",  # Add this
]
```

### Step 2: Create Parser File

Create `repotoire/parsers/tree_sitter_typescript.py`:

```python
"""Tree-sitter TypeScript parser."""

from repotoire.parsers.base_tree_sitter_parser import BaseTreeSitterParser
from repotoire.parsers.tree_sitter_adapter import TreeSitterAdapter


class TreeSitterTypeScriptParser(BaseTreeSitterParser):
    """TypeScript parser using tree-sitter adapter."""

    def __init__(self):
        """Initialize TypeScript parser."""
        from tree_sitter_typescript import language_typescript

        adapter = TreeSitterAdapter(language_typescript())

        # TypeScript node type mappings
        node_mappings = {
            "class": "class_declaration",
            "function": "function_declaration",
            "import": "import_statement",
            "call": "call_expression",
        }

        super().__init__(
            adapter=adapter,
            language_name="typescript",
            node_mappings=node_mappings
        )
```

That's it! You get entity extraction for free via `BaseTreeSitterParser`.

### Step 3: Override Language-Specific Extraction (Optional)

If the language has unique features, override specific methods:

```python
class TreeSitterTypeScriptParser(BaseTreeSitterParser):
    # ... __init__ from above ...

    def _extract_docstring(self, node):
        """Extract JSDoc comments from TypeScript."""
        # TypeScript uses JSDoc comments like /** ... */
        for child in node.children:
            if child.node_type == "comment":
                text = child.text.strip()
                if text.startswith("/**") and text.endswith("*/"):
                    return text[3:-2].strip()
        return None

    def _is_async_function(self, func_node):
        """Check for async keyword in TypeScript."""
        for child in func_node.children:
            if child.node_type == "async" or child.text == "async":
                return True
        return False
```

### Step 4: Register Parser in Pipeline

Edit `repotoire/pipeline/ingestion.py`:

```python
from repotoire.parsers.tree_sitter_typescript import TreeSitterTypeScriptParser

class IngestionPipeline:
    def __init__(self, ...):
        # Register parsers
        self.parsers = {
            ".py": PythonParser(),
            ".ts": TreeSitterTypeScriptParser(),  # Add this
            ".tsx": TreeSitterTypeScriptParser(), # Also for TSX
        }
```

### Step 5: Write Tests

Create `tests/unit/parsers/test_typescript_parser.py`:

```python
import pytest
from repotoire.parsers.tree_sitter_typescript import TreeSitterTypeScriptParser


@pytest.mark.skipif(
    not pytest.importorskip("tree_sitter_typescript"),
    reason="tree-sitter-typescript not installed"
)
class TestTypeScriptParser:
    def test_parse_interface(self):
        parser = TreeSitterTypeScriptParser()
        source = '''
interface User {
    name: string;
    age: number;
}
'''
        tree = parser.adapter.parse(source)
        interfaces = tree.find_all("interface_declaration")

        assert len(interfaces) == 1
        assert interfaces[0].get_field("name").text == "User"
```

## Advanced: Language-Specific Node Type Mappings

Different tree-sitter grammars use different node type names. Here's a reference:

### Python
```python
node_mappings = {
    "class": "class_definition",
    "function": "function_definition",
    "import": "import_statement",
    "import_from": "import_from_statement",
    "call": "call",
}
```

### TypeScript/JavaScript
```python
node_mappings = {
    "class": "class_declaration",
    "function": "function_declaration",
    "import": "import_statement",
    "call": "call_expression",
}
```

### Java
```python
node_mappings = {
    "class": "class_declaration",
    "function": "method_declaration",
    "import": "import_declaration",
    "call": "method_invocation",
}
```

### Go
```python
node_mappings = {
    "class": "type_declaration",  # Go doesn't have classes, uses types
    "function": "function_declaration",
    "import": "import_declaration",
    "call": "call_expression",
}
```

## What You Get for Free

By extending `BaseTreeSitterParser`, you automatically get:

✅ **Entity extraction**: Files, classes, functions with metadata
✅ **Docstring extraction**: First string literal in function body
✅ **Complexity calculation**: Cyclomatic complexity via decision point counting
✅ **Base class detection**: Inheritance relationships
✅ **Import tracking**: IMPORTS relationships (naive implementation)
✅ **Call tracking**: CALLS relationships (naive implementation)
✅ **File metadata**: Hash, LOC, modification time

## Common Issues and Solutions

### Issue: "ModuleNotFoundError: No module named 'tree_sitter_X'"

**Solution**: Install the tree-sitter language package:
```bash
pip install tree-sitter-python  # or tree-sitter-typescript, etc.
```

### Issue: "No nodes found for class_definition"

**Problem**: Wrong node type name for this language.

**Solution**: Use tree-sitter playground or inspect node types:
```python
tree = parser.adapter.parse(source)
for node in tree.walk():
    print(f"{node.node_type}: {node.text[:30]}")
```

### Issue: Relationship extraction returns wrong module names

**Problem**: The base implementation is naive and doesn't handle:
- Relative imports (`from .module import foo`)
- Package hierarchies (`from package.submodule import foo`)
- Import aliases (`import foo as bar`)

**Solution**: Override `_extract_import_names()` for your language:

```python
def _extract_import_names(self, import_node):
    """TypeScript-specific import extraction."""
    # Handle: import { foo } from 'module'
    module_node = import_node.get_field("source")
    if module_node and module_node.node_type == "string":
        return [module_node.text.strip('"').strip("'")]
    return []
```

### Issue: Function calls extract wrong names

**Problem**: Base implementation can't distinguish between:
- Function calls: `foo()`
- Method calls: `obj.method()`
- Constructor calls: `new Class()`
- Dynamic calls: `functions[key]()`

**Solution**: Override `_extract_call_name()` with language-specific logic.

## Performance Considerations

**Tree-sitter is fast**, but there are gotchas:

| Operation | Time Complexity | Notes |
|-----------|----------------|-------|
| Parse file | O(n) | Linear in file size |
| `find_all()` | O(n) | Traverses entire tree |
| `get_field()` | O(1) | Cached after first access |

**Optimization tips**:
- Cache parsed trees if processing multiple times
- Use `find_first()` instead of `find_all()` when you only need one match
- Batch file processing to amortize initialization costs

## Real-World Example: Adding Rust Support

Here's a complete example adding Rust:

```python
# repotoire/parsers/tree_sitter_rust.py
from repotoire.parsers.base_tree_sitter_parser import BaseTreeSitterParser
from repotoire.parsers.tree_sitter_adapter import TreeSitterAdapter
from typing import List, Optional


class TreeSitterRustParser(BaseTreeSitterParser):
    """Rust parser using tree-sitter adapter."""

    def __init__(self):
        from tree_sitter_rust import language

        adapter = TreeSitterAdapter(language())

        node_mappings = {
            "class": "struct_item",  # Rust uses structs
            "function": "function_item",
            "import": "use_declaration",
            "call": "call_expression",
        }

        super().__init__(
            adapter=adapter,
            language_name="rust",
            node_mappings=node_mappings
        )

    def _extract_docstring(self, node):
        """Extract Rust doc comments (///)."""
        # Rust doc comments are /// before the item
        # They're in preceding_comment nodes
        for child in node.children:
            if "comment" in child.node_type and child.text.startswith("///"):
                lines = [
                    line.strip()[3:].strip()
                    for line in child.text.split('\n')
                    if line.strip().startswith("///")
                ]
                return '\n'.join(lines)
        return None

    def _extract_base_classes(self, struct_node):
        """Rust structs don't have inheritance, return empty."""
        return []

    def _is_async_function(self, func_node):
        """Check for async fn in Rust."""
        # Rust uses "async_function_item" node type
        return func_node.node_type == "async_function_item"
```

Then register it:

```python
# In ingestion.py
self.parsers = {
    ".py": PythonParser(),
    ".rs": TreeSitterRustParser(),
}
```

## Debugging Tips

### Inspect the AST

```python
parser = TreeSitterTypeScriptParser()
tree = parser.adapter.parse(source_code)

# Print all node types
for node in tree.walk():
    print(f"{node.node_type} @ line {node.start_line}: {node.text[:50]}")
```

### Use Tree-Sitter Playground

Visit https://tree-sitter.github.io/tree-sitter/playground to:
- See AST structure visually
- Identify correct node type names
- Test your queries before implementing

### Enable Debug Logging

```python
import logging
logging.getLogger("repotoire.parsers").setLevel(logging.DEBUG)

parser = TreeSitterTypeScriptParser()
# ... will now print debug messages
```

## Contributing

When adding a new language:

1. Create parser file in `repotoire/parsers/`
2. Add tests in `tests/unit/parsers/`
3. Update `pyproject.toml` optional dependencies
4. Update this documentation with node mappings
5. Submit PR with examples

## References

- [Tree-sitter Documentation](https://tree-sitter.github.io/tree-sitter/)
- [Available Language Bindings](https://github.com/tree-sitter)
- [Node Type Reference](https://github.com/tree-sitter/tree-sitter/wiki/Node-Types)

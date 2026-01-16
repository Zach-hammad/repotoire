"""Tests for tree-sitter TypeScript/JavaScript parser."""

import pytest
import tempfile
from pathlib import Path

# Skip all tests if tree-sitter-typescript not available
pytestmark = pytest.mark.skipif(
    not pytest.importorskip("tree_sitter_typescript", reason="tree-sitter-typescript not installed"),
    reason="tree-sitter-typescript not available"
)


@pytest.fixture
def ts_parser():
    """Create a TypeScript parser."""
    from repotoire.parsers.tree_sitter_typescript import TreeSitterTypeScriptParser
    return TreeSitterTypeScriptParser()


@pytest.fixture
def js_parser():
    """Create a JavaScript parser."""
    pytest.importorskip("tree_sitter_javascript", reason="tree-sitter-javascript not installed")
    from repotoire.parsers.tree_sitter_typescript import TreeSitterJavaScriptParser
    return TreeSitterJavaScriptParser()


class TestTreeSitterTypeScriptParser:
    """Test TreeSitterTypeScriptParser functionality."""

    def test_parser_initialization(self, ts_parser):
        """Test parser can be initialized."""
        assert ts_parser.language_name == "typescript"
        assert ts_parser.adapter is not None

    def test_parse_simple_function(self, ts_parser):
        """Test parsing a simple TypeScript function."""
        source = '''function hello(name: string): string {
    return `Hello, ${name}!`;
}
'''
        tree = ts_parser.adapter.parse(source)

        assert tree.node_type == "program"
        funcs = tree.find_all("function_declaration")
        assert len(funcs) == 1

    def test_parse_class_with_methods(self, ts_parser):
        """Test parsing a class with methods."""
        source = '''class Calculator {
    add(a: number, b: number): number {
        return a + b;
    }

    subtract(a: number, b: number): number {
        return a - b;
    }
}
'''
        tree = ts_parser.adapter.parse(source)

        classes = tree.find_all("class_declaration")
        assert len(classes) == 1

    def test_extract_entities_from_class(self, ts_parser):
        """Test entity extraction from TypeScript class."""
        source = '''class UserService {
    private users: User[] = [];

    async getUser(id: string): Promise<User> {
        return this.users.find(u => u.id === id);
    }

    addUser(user: User): void {
        this.users.push(user);
    }
}
'''
        with tempfile.NamedTemporaryFile(suffix='.ts', mode='w', delete=False) as f:
            f.write(source)
            temp_path = f.name

        try:
            tree = ts_parser.parse(temp_path)
            entities = ts_parser.extract_entities(tree, temp_path)

            # Should have: FileEntity, ClassEntity, 2x FunctionEntity
            entity_types = [e.__class__.__name__ for e in entities]
            assert "FileEntity" in entity_types
            assert "ClassEntity" in entity_types
            assert entity_types.count("FunctionEntity") == 2

            # Check class name
            class_entities = [e for e in entities if e.__class__.__name__ == "ClassEntity"]
            assert len(class_entities) == 1
            assert class_entities[0].name == "UserService"

            # Check method names
            func_entities = [e for e in entities if e.__class__.__name__ == "FunctionEntity"]
            func_names = {e.name for e in func_entities}
            assert func_names == {"getUser", "addUser"}

            # Check async detection
            get_user = next(e for e in func_entities if e.name == "getUser")
            assert get_user.is_async is True

            add_user = next(e for e in func_entities if e.name == "addUser")
            assert add_user.is_async is False
        finally:
            Path(temp_path).unlink()

    def test_extract_inheritance(self, ts_parser):
        """Test inheritance extraction."""
        source = '''class Animal {
    name: string;
}

class Dog extends Animal {
    bark(): void {
        console.log("Woof!");
    }
}
'''
        with tempfile.NamedTemporaryFile(suffix='.ts', mode='w', delete=False) as f:
            f.write(source)
            temp_path = f.name

        try:
            tree = ts_parser.parse(temp_path)
            entities = ts_parser.extract_entities(tree, temp_path)
            relationships = ts_parser.extract_relationships(tree, temp_path, entities)

            # Find INHERITS relationship
            inherits_rels = [r for r in relationships if r.rel_type.value == "INHERITS"]
            assert len(inherits_rels) == 1

            # Check inheritance target (may be qualified or unqualified)
            assert inherits_rels[0].target_id.endswith("Animal")
        finally:
            Path(temp_path).unlink()

    def test_extract_imports(self, ts_parser):
        """Test import extraction."""
        source = '''import { User, Role } from "./models";
import axios from "axios";
import * as utils from "./utils";

class Service {}
'''
        with tempfile.NamedTemporaryFile(suffix='.ts', mode='w', delete=False) as f:
            f.write(source)
            temp_path = f.name

        try:
            tree = ts_parser.parse(temp_path)
            entities = ts_parser.extract_entities(tree, temp_path)
            relationships = ts_parser.extract_relationships(tree, temp_path, entities)

            # Find IMPORTS relationships
            import_rels = [r for r in relationships if r.rel_type.value == "IMPORTS"]
            assert len(import_rels) >= 3  # At least 3 imports

            # Check import targets
            import_targets = {r.target_id for r in import_rels}
            assert "axios" in import_targets
            assert "./utils" in import_targets
        finally:
            Path(temp_path).unlink()

    def test_extract_arrow_function(self, ts_parser):
        """Test arrow function extraction."""
        source = '''const greet = (name: string): string => {
    return `Hello, ${name}`;
};

const add = (a: number, b: number) => a + b;
'''
        with tempfile.NamedTemporaryFile(suffix='.ts', mode='w', delete=False) as f:
            f.write(source)
            temp_path = f.name

        try:
            tree = ts_parser.parse(temp_path)
            entities = ts_parser.extract_entities(tree, temp_path)

            # Should find arrow functions as entities
            func_entities = [e for e in entities if e.__class__.__name__ == "FunctionEntity"]
            func_names = {e.name for e in func_entities}
            assert "greet" in func_names
            assert "add" in func_names
        finally:
            Path(temp_path).unlink()

    def test_extract_exported_function(self, ts_parser):
        """Test exported function extraction."""
        source = '''export function createService(): Service {
    return new Service();
}

export const helper = () => {};
'''
        with tempfile.NamedTemporaryFile(suffix='.ts', mode='w', delete=False) as f:
            f.write(source)
            temp_path = f.name

        try:
            tree = ts_parser.parse(temp_path)
            entities = ts_parser.extract_entities(tree, temp_path)

            func_entities = [e for e in entities if e.__class__.__name__ == "FunctionEntity"]
            func_names = {e.name for e in func_entities}
            assert "createService" in func_names
        finally:
            Path(temp_path).unlink()

    def test_complexity_calculation(self, ts_parser):
        """Test cyclomatic complexity calculation."""
        source = '''function complexFunction(x: number): string {
    if (x > 0) {
        if (x > 10) {
            return "big";
        } else {
            return "medium";
        }
    } else if (x < 0) {
        return "negative";
    }
    return "zero";
}
'''
        with tempfile.NamedTemporaryFile(suffix='.ts', mode='w', delete=False) as f:
            f.write(source)
            temp_path = f.name

        try:
            tree = ts_parser.parse(temp_path)
            entities = ts_parser.extract_entities(tree, temp_path)

            func_entity = next(e for e in entities if e.__class__.__name__ == "FunctionEntity")
            # Base complexity (1) + 3 if statements + 1 else_clause
            assert func_entity.complexity >= 3
        finally:
            Path(temp_path).unlink()


class TestTreeSitterJavaScriptParser:
    """Test TreeSitterJavaScriptParser functionality."""

    def test_parser_initialization(self, js_parser):
        """Test parser can be initialized."""
        assert js_parser.language_name == "javascript"
        assert js_parser.adapter is not None

    def test_parse_javascript_class(self, js_parser):
        """Test parsing a JavaScript class."""
        source = '''class Calculator {
    constructor() {
        this.result = 0;
    }

    add(a, b) {
        return a + b;
    }
}
'''
        with tempfile.NamedTemporaryFile(suffix='.js', mode='w', delete=False) as f:
            f.write(source)
            temp_path = f.name

        try:
            tree = js_parser.parse(temp_path)
            entities = js_parser.extract_entities(tree, temp_path)

            class_entities = [e for e in entities if e.__class__.__name__ == "ClassEntity"]
            assert len(class_entities) == 1
            assert class_entities[0].name == "Calculator"
        finally:
            Path(temp_path).unlink()

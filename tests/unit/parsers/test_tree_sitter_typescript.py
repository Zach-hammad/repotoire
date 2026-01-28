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


class TestJSDocExtraction:
    """Tests for JSDoc comment extraction."""

    @pytest.fixture
    def ts_parser(self):
        """Create a TypeScript parser."""
        from repotoire.parsers.tree_sitter_typescript import TreeSitterTypeScriptParser
        return TreeSitterTypeScriptParser()

    def test_jsdoc_on_function(self, ts_parser):
        """Test JSDoc extraction from function declaration."""
        source = '''/**
 * Calculates the sum of two numbers.
 * @param a First number
 * @param b Second number
 * @returns The sum of a and b
 */
function add(a: number, b: number): number {
    return a + b;
}
'''
        with tempfile.NamedTemporaryFile(suffix='.ts', mode='w', delete=False) as f:
            f.write(source)
            temp_path = f.name

        try:
            tree = ts_parser.parse(temp_path)
            entities = ts_parser.extract_entities(tree, temp_path)

            func = next((e for e in entities if e.name == 'add'), None)
            assert func is not None
            assert func.docstring is not None
            assert "Calculates the sum" in func.docstring
            assert "@param a" in func.docstring
            assert "@returns" in func.docstring
        finally:
            Path(temp_path).unlink()

    def test_jsdoc_on_class(self, ts_parser):
        """Test JSDoc extraction from class declaration."""
        source = '''/**
 * Represents a user in the system.
 * @class
 */
class User {
    name: string;
}
'''
        with tempfile.NamedTemporaryFile(suffix='.ts', mode='w', delete=False) as f:
            f.write(source)
            temp_path = f.name

        try:
            tree = ts_parser.parse(temp_path)
            entities = ts_parser.extract_entities(tree, temp_path)

            cls = next((e for e in entities if e.name == 'User'), None)
            assert cls is not None
            assert cls.docstring is not None
            assert "Represents a user" in cls.docstring
        finally:
            Path(temp_path).unlink()

    def test_no_jsdoc_returns_none(self, ts_parser):
        """Test that functions without JSDoc return None for docstring."""
        source = '''function noDoc(x: number): number {
    return x * 2;
}
'''
        with tempfile.NamedTemporaryFile(suffix='.ts', mode='w', delete=False) as f:
            f.write(source)
            temp_path = f.name

        try:
            tree = ts_parser.parse(temp_path)
            entities = ts_parser.extract_entities(tree, temp_path)

            func = next((e for e in entities if e.name == 'noDoc'), None)
            assert func is not None
            assert func.docstring is None
        finally:
            Path(temp_path).unlink()

    def test_regular_comment_not_jsdoc(self, ts_parser):
        """Test that regular comments are not treated as JSDoc."""
        source = '''// This is a regular comment
function regularComment(x: number): number {
    return x;
}
'''
        with tempfile.NamedTemporaryFile(suffix='.ts', mode='w', delete=False) as f:
            f.write(source)
            temp_path = f.name

        try:
            tree = ts_parser.parse(temp_path)
            entities = ts_parser.extract_entities(tree, temp_path)

            func = next((e for e in entities if e.name == 'regularComment'), None)
            assert func is not None
            # Regular // comments should not be extracted as docstrings
            assert func.docstring is None
        finally:
            Path(temp_path).unlink()


class TestReactPatternDetection:
    """Tests for React hooks and component detection."""

    @pytest.fixture
    def tsx_parser(self):
        """Create a TSX parser for React components."""
        from repotoire.parsers.tree_sitter_typescript import TreeSitterTypeScriptParser
        return TreeSitterTypeScriptParser(use_tsx=True)

    def test_detect_usestate_hook(self, tsx_parser):
        """Test detection of useState hook."""
        source = '''function Counter() {
    const [count, setCount] = useState(0);
    return <div>{count}</div>;
}
'''
        with tempfile.NamedTemporaryFile(suffix='.tsx', mode='w', delete=False) as f:
            f.write(source)
            temp_path = f.name

        try:
            tree = tsx_parser.parse(temp_path)
            entities = tsx_parser.extract_entities(tree, temp_path)

            func = next((e for e in entities if e.name == 'Counter'), None)
            assert func is not None
            assert 'react_hooks' in func.metadata
            assert 'useState' in func.metadata['react_hooks']
        finally:
            Path(temp_path).unlink()

    def test_detect_multiple_hooks(self, tsx_parser):
        """Test detection of multiple React hooks."""
        source = '''function App() {
    const [data, setData] = useState(null);
    const [loading, setLoading] = useState(true);

    useEffect(() => {
        fetchData().then(setData);
    }, []);

    const memoizedValue = useMemo(() => data?.length, [data]);
    const callback = useCallback(() => setLoading(true), []);

    return <div>{loading ? 'Loading...' : data}</div>;
}
'''
        with tempfile.NamedTemporaryFile(suffix='.tsx', mode='w', delete=False) as f:
            f.write(source)
            temp_path = f.name

        try:
            tree = tsx_parser.parse(temp_path)
            entities = tsx_parser.extract_entities(tree, temp_path)

            func = next((e for e in entities if e.name == 'App'), None)
            assert func is not None

            hooks = func.metadata.get('react_hooks', [])
            assert 'useState' in hooks
            assert 'useEffect' in hooks
            assert 'useMemo' in hooks
            assert 'useCallback' in hooks
        finally:
            Path(temp_path).unlink()

    def test_detect_react_component(self, tsx_parser):
        """Test detection of React functional component."""
        source = '''function Button({ onClick, children }) {
    return <button onClick={onClick}>{children}</button>;
}
'''
        with tempfile.NamedTemporaryFile(suffix='.tsx', mode='w', delete=False) as f:
            f.write(source)
            temp_path = f.name

        try:
            tree = tsx_parser.parse(temp_path)
            entities = tsx_parser.extract_entities(tree, temp_path)

            func = next((e for e in entities if e.name == 'Button'), None)
            assert func is not None
            assert func.metadata.get('is_react_component') is True
            assert func.metadata.get('component_type') == 'functional'
        finally:
            Path(temp_path).unlink()

    def test_detect_component_with_hooks(self, tsx_parser):
        """Test detection of component with hooks."""
        source = '''function SearchInput() {
    const [query, setQuery] = useState('');
    const inputRef = useRef(null);

    return <input ref={inputRef} value={query} onChange={e => setQuery(e.target.value)} />;
}
'''
        with tempfile.NamedTemporaryFile(suffix='.tsx', mode='w', delete=False) as f:
            f.write(source)
            temp_path = f.name

        try:
            tree = tsx_parser.parse(temp_path)
            entities = tsx_parser.extract_entities(tree, temp_path)

            func = next((e for e in entities if e.name == 'SearchInput'), None)
            assert func is not None
            assert func.metadata.get('is_react_component') is True
            assert func.metadata.get('component_type') == 'functional_with_hooks'
            assert 'useState' in func.metadata.get('react_hooks', [])
            assert 'useRef' in func.metadata.get('react_hooks', [])
        finally:
            Path(temp_path).unlink()

    def test_detect_custom_hooks(self, tsx_parser):
        """Test detection of custom hooks."""
        source = '''function useCustomHook(initialValue) {
    const [value, setValue] = useState(initialValue);

    useEffect(() => {
        console.log('Value changed:', value);
    }, [value]);

    return [value, setValue];
}

function Component() {
    const [val, setVal] = useCustomHook('test');
    return <div>{val}</div>;
}
'''
        with tempfile.NamedTemporaryFile(suffix='.tsx', mode='w', delete=False) as f:
            f.write(source)
            temp_path = f.name

        try:
            tree = tsx_parser.parse(temp_path)
            entities = tsx_parser.extract_entities(tree, temp_path)

            # Custom hook itself uses hooks
            custom_hook = next((e for e in entities if e.name == 'useCustomHook'), None)
            assert custom_hook is not None
            assert 'useState' in custom_hook.metadata.get('react_hooks', [])

            # Component uses custom hook
            component = next((e for e in entities if e.name == 'Component'), None)
            assert component is not None
            assert 'useCustomHook' in component.metadata.get('react_hooks', [])
        finally:
            Path(temp_path).unlink()

    def test_arrow_component_with_hooks(self, tsx_parser):
        """Test arrow function component with hooks."""
        source = '''const Toggle = () => {
    const [isOn, setIsOn] = useState(false);
    return <button onClick={() => setIsOn(!isOn)}>{isOn ? 'ON' : 'OFF'}</button>;
};
'''
        with tempfile.NamedTemporaryFile(suffix='.tsx', mode='w', delete=False) as f:
            f.write(source)
            temp_path = f.name

        try:
            tree = tsx_parser.parse(temp_path)
            entities = tsx_parser.extract_entities(tree, temp_path)

            func = next((e for e in entities if e.name == 'Toggle'), None)
            assert func is not None
            assert func.metadata.get('is_react_component') is True
            assert 'useState' in func.metadata.get('react_hooks', [])
        finally:
            Path(temp_path).unlink()

    def test_non_component_function(self, tsx_parser):
        """Test that non-component functions are not marked as components."""
        source = '''function calculateSum(a: number, b: number): number {
    return a + b;
}
'''
        with tempfile.NamedTemporaryFile(suffix='.tsx', mode='w', delete=False) as f:
            f.write(source)
            temp_path = f.name

        try:
            tree = tsx_parser.parse(temp_path)
            entities = tsx_parser.extract_entities(tree, temp_path)

            func = next((e for e in entities if e.name == 'calculateSum'), None)
            assert func is not None
            assert func.metadata.get('is_react_component') is not True
        finally:
            Path(temp_path).unlink()


class TestNestedCallDetection:
    """Tests for nested and chained function call detection."""

    @pytest.fixture
    def ts_parser(self):
        """Create a TypeScript parser."""
        from repotoire.parsers.tree_sitter_typescript import TreeSitterTypeScriptParser
        return TreeSitterTypeScriptParser()

    def test_simple_call(self, ts_parser):
        """Test simple function call extraction."""
        source = '''function main() {
    console.log("hello");
    doSomething();
}
'''
        with tempfile.NamedTemporaryFile(suffix='.ts', mode='w', delete=False) as f:
            f.write(source)
            temp_path = f.name

        try:
            tree = ts_parser.parse(temp_path)
            entities = ts_parser.extract_entities(tree, temp_path)
            relationships = ts_parser.extract_relationships(tree, temp_path, entities)

            call_rels = [r for r in relationships if r.rel_type.value == "CALLS"]
            called_names = {r.target_id for r in call_rels}

            assert 'log' in called_names or 'doSomething' in called_names
        finally:
            Path(temp_path).unlink()

    def test_method_call(self, ts_parser):
        """Test method call extraction (obj.method())."""
        source = '''function processData() {
    const result = data.filter(x => x > 0).map(x => x * 2);
    return result;
}
'''
        with tempfile.NamedTemporaryFile(suffix='.ts', mode='w', delete=False) as f:
            f.write(source)
            temp_path = f.name

        try:
            tree = ts_parser.parse(temp_path)
            entities = ts_parser.extract_entities(tree, temp_path)
            relationships = ts_parser.extract_relationships(tree, temp_path, entities)

            call_rels = [r for r in relationships if r.rel_type.value == "CALLS"]
            called_names = {r.target_id for r in call_rels}

            # Should detect filter and map calls
            assert 'filter' in called_names or 'map' in called_names
        finally:
            Path(temp_path).unlink()

    def test_nested_call(self, ts_parser):
        """Test nested function call extraction (func1(func2()))."""
        source = '''function nested() {
    const result = processResult(fetchData());
    return result;
}
'''
        with tempfile.NamedTemporaryFile(suffix='.ts', mode='w', delete=False) as f:
            f.write(source)
            temp_path = f.name

        try:
            tree = ts_parser.parse(temp_path)
            entities = ts_parser.extract_entities(tree, temp_path)
            relationships = ts_parser.extract_relationships(tree, temp_path, entities)

            call_rels = [r for r in relationships if r.rel_type.value == "CALLS"]
            called_names = {r.target_id for r in call_rels}

            # Should detect both processResult and fetchData
            assert 'processResult' in called_names
            assert 'fetchData' in called_names
        finally:
            Path(temp_path).unlink()


class TestNestingLevelTracking:
    """Tests for nesting level calculation."""

    @pytest.fixture
    def ts_parser(self):
        """Create a TypeScript parser."""
        from repotoire.parsers.tree_sitter_typescript import TreeSitterTypeScriptParser
        return TreeSitterTypeScriptParser()

    def test_top_level_class(self, ts_parser):
        """Test that top-level class has nesting level 0."""
        source = '''class TopLevel {
    method() {}
}
'''
        with tempfile.NamedTemporaryFile(suffix='.ts', mode='w', delete=False) as f:
            f.write(source)
            temp_path = f.name

        try:
            tree = ts_parser.parse(temp_path)
            entities = ts_parser.extract_entities(tree, temp_path)

            cls = next((e for e in entities if e.name == 'TopLevel'), None)
            assert cls is not None
            assert cls.nesting_level == 0
        finally:
            Path(temp_path).unlink()

    def test_multiple_top_level_classes(self, ts_parser):
        """Test multiple top-level classes all have nesting level 0."""
        source = '''class First {
    foo() {}
}

class Second {
    bar() {}
}

class Third {
    baz() {}
}
'''
        with tempfile.NamedTemporaryFile(suffix='.ts', mode='w', delete=False) as f:
            f.write(source)
            temp_path = f.name

        try:
            tree = ts_parser.parse(temp_path)
            entities = ts_parser.extract_entities(tree, temp_path)

            classes = [e for e in entities if e.__class__.__name__ == 'ClassEntity']
            assert len(classes) == 3

            for cls in classes:
                assert cls.nesting_level == 0, f"Class {cls.name} should have nesting level 0"
        finally:
            Path(temp_path).unlink()


class TestGenericFunctionDeclarations:
    """Tests for type parameter extraction from function declarations."""

    @pytest.fixture
    def ts_parser(self):
        """Create a TypeScript parser."""
        from repotoire.parsers.tree_sitter_typescript import TreeSitterTypeScriptParser
        return TreeSitterTypeScriptParser()

    def test_generic_function_declaration(self, ts_parser):
        """Test type parameter extraction from regular function declaration."""
        source = '''function identity<T>(value: T): T {
    return value;
}
'''
        with tempfile.NamedTemporaryFile(suffix='.ts', mode='w', delete=False) as f:
            f.write(source)
            temp_path = f.name

        try:
            tree = ts_parser.parse(temp_path)
            entities = ts_parser.extract_entities(tree, temp_path)

            func = next((e for e in entities if e.name == 'identity'), None)
            assert func is not None
            assert func.metadata is not None
            assert 'type_parameters' in func.metadata
            assert func.metadata['is_generic'] is True

            type_params = func.metadata['type_parameters']
            assert len(type_params) == 1
            assert type_params[0]['name'] == 'T'
        finally:
            Path(temp_path).unlink()

    def test_generic_function_with_constraint(self, ts_parser):
        """Test type parameter extraction with extends constraint."""
        source = '''function getLength<T extends { length: number }>(item: T): number {
    return item.length;
}
'''
        with tempfile.NamedTemporaryFile(suffix='.ts', mode='w', delete=False) as f:
            f.write(source)
            temp_path = f.name

        try:
            tree = ts_parser.parse(temp_path)
            entities = ts_parser.extract_entities(tree, temp_path)

            func = next((e for e in entities if e.name == 'getLength'), None)
            assert func is not None
            assert func.metadata is not None
            assert 'type_parameters' in func.metadata
            assert func.metadata['is_generic'] is True

            type_params = func.metadata['type_parameters']
            assert len(type_params) == 1
            assert type_params[0]['name'] == 'T'
            # Constraint should be captured
            assert type_params[0]['constraint'] is not None
        finally:
            Path(temp_path).unlink()

    def test_generic_function_multiple_type_params(self, ts_parser):
        """Test function with multiple type parameters."""
        source = '''function pair<K, V>(key: K, value: V): [K, V] {
    return [key, value];
}
'''
        with tempfile.NamedTemporaryFile(suffix='.ts', mode='w', delete=False) as f:
            f.write(source)
            temp_path = f.name

        try:
            tree = ts_parser.parse(temp_path)
            entities = ts_parser.extract_entities(tree, temp_path)

            func = next((e for e in entities if e.name == 'pair'), None)
            assert func is not None
            assert func.metadata is not None
            assert 'type_parameters' in func.metadata

            type_params = func.metadata['type_parameters']
            assert len(type_params) == 2
            param_names = [tp['name'] for tp in type_params]
            assert 'K' in param_names
            assert 'V' in param_names
        finally:
            Path(temp_path).unlink()

    def test_const_type_parameter_function(self, ts_parser):
        """Test TypeScript 5+ const type parameter in function declaration."""
        source = '''function asConst<const T>(value: T): T {
    return value;
}
'''
        with tempfile.NamedTemporaryFile(suffix='.ts', mode='w', delete=False) as f:
            f.write(source)
            temp_path = f.name

        try:
            tree = ts_parser.parse(temp_path)
            entities = ts_parser.extract_entities(tree, temp_path)

            func = next((e for e in entities if e.name == 'asConst'), None)
            assert func is not None
            assert func.metadata is not None
            assert 'type_parameters' in func.metadata
            assert func.metadata['has_const_type_params'] is True

            type_params = func.metadata['type_parameters']
            assert len(type_params) == 1
            assert type_params[0]['name'] == 'T'
            assert type_params[0]['is_const'] is True
        finally:
            Path(temp_path).unlink()

    def test_exported_generic_function(self, ts_parser):
        """Test type parameter extraction from exported function."""
        source = '''export function map<T, U>(arr: T[], fn: (item: T) => U): U[] {
    return arr.map(fn);
}
'''
        with tempfile.NamedTemporaryFile(suffix='.ts', mode='w', delete=False) as f:
            f.write(source)
            temp_path = f.name

        try:
            tree = ts_parser.parse(temp_path)
            entities = ts_parser.extract_entities(tree, temp_path)

            func = next((e for e in entities if e.name == 'map'), None)
            assert func is not None
            assert func.metadata is not None
            assert 'type_parameters' in func.metadata

            type_params = func.metadata['type_parameters']
            assert len(type_params) == 2
        finally:
            Path(temp_path).unlink()

    def test_generic_async_function(self, ts_parser):
        """Test type parameter extraction from async function declaration."""
        source = '''async function fetchData<T>(url: string): Promise<T> {
    const response = await fetch(url);
    return response.json();
}
'''
        with tempfile.NamedTemporaryFile(suffix='.ts', mode='w', delete=False) as f:
            f.write(source)
            temp_path = f.name

        try:
            tree = ts_parser.parse(temp_path)
            entities = ts_parser.extract_entities(tree, temp_path)

            func = next((e for e in entities if e.name == 'fetchData'), None)
            assert func is not None
            assert func.is_async is True
            assert func.metadata is not None
            assert 'type_parameters' in func.metadata

            type_params = func.metadata['type_parameters']
            assert len(type_params) == 1
            assert type_params[0]['name'] == 'T'
        finally:
            Path(temp_path).unlink()

    def test_generic_method_in_class(self, ts_parser):
        """Test type parameter extraction from class method."""
        source = '''class Repository {
    findOne<T>(id: string): T | null {
        return null;
    }

    findMany<T, K extends keyof T>(criteria: Pick<T, K>): T[] {
        return [];
    }
}
'''
        with tempfile.NamedTemporaryFile(suffix='.ts', mode='w', delete=False) as f:
            f.write(source)
            temp_path = f.name

        try:
            tree = ts_parser.parse(temp_path)
            entities = ts_parser.extract_entities(tree, temp_path)

            # Check findOne method
            find_one = next((e for e in entities if e.name == 'findOne'), None)
            assert find_one is not None
            assert find_one.metadata is not None
            assert 'type_parameters' in find_one.metadata
            assert len(find_one.metadata['type_parameters']) == 1
            assert find_one.metadata['type_parameters'][0]['name'] == 'T'

            # Check findMany method with multiple type params
            find_many = next((e for e in entities if e.name == 'findMany'), None)
            assert find_many is not None
            assert find_many.metadata is not None
            assert 'type_parameters' in find_many.metadata
            assert len(find_many.metadata['type_parameters']) == 2
        finally:
            Path(temp_path).unlink()

    def test_non_generic_function_has_no_type_params(self, ts_parser):
        """Test that non-generic functions don't have type_parameters in metadata."""
        source = '''function add(a: number, b: number): number {
    return a + b;
}
'''
        with tempfile.NamedTemporaryFile(suffix='.ts', mode='w', delete=False) as f:
            f.write(source)
            temp_path = f.name

        try:
            tree = ts_parser.parse(temp_path)
            entities = ts_parser.extract_entities(tree, temp_path)

            func = next((e for e in entities if e.name == 'add'), None)
            assert func is not None
            # Non-generic functions should not have type_parameters
            if func.metadata:
                assert 'type_parameters' not in func.metadata
        finally:
            Path(temp_path).unlink()

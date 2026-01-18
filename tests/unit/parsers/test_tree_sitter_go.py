"""Tests for tree-sitter Go parser."""

import pytest
import tempfile
from pathlib import Path

# Skip all tests if tree-sitter-go not available
pytestmark = pytest.mark.skipif(
    not pytest.importorskip("tree_sitter_go", reason="tree-sitter-go not installed"),
    reason="tree-sitter-go not available"
)


@pytest.fixture
def go_parser():
    """Create a Go parser."""
    from repotoire.parsers.tree_sitter_go import TreeSitterGoParser
    return TreeSitterGoParser()


class TestTreeSitterGoParser:
    """Test TreeSitterGoParser functionality."""

    def test_parser_initialization(self, go_parser):
        """Test parser can be initialized."""
        assert go_parser.language_name == "go"
        assert go_parser.adapter is not None

    def test_parse_simple_function(self, go_parser):
        """Test parsing a simple Go function."""
        source = '''package main

import "fmt"

func main() {
    fmt.Println("Hello, World!")
}
'''
        tree = go_parser.adapter.parse(source)

        assert tree.node_type == "source_file"
        funcs = tree.find_all("function_declaration")
        assert len(funcs) == 1

    def test_parse_struct(self, go_parser):
        """Test parsing a Go struct."""
        source = '''package main

type User struct {
    Name  string
    Email string
    Age   int
}
'''
        tree = go_parser.adapter.parse(source)

        types = tree.find_all("type_declaration")
        assert len(types) == 1

    def test_extract_entities_from_struct(self, go_parser):
        """Test entity extraction from Go struct with methods."""
        source = '''package main

type Calculator struct {
    value int
}

func (c *Calculator) Add(n int) {
    c.value += n
}

func (c Calculator) GetValue() int {
    return c.value
}
'''
        with tempfile.NamedTemporaryFile(suffix='.go', mode='w', delete=False) as f:
            f.write(source)
            temp_path = f.name

        try:
            tree = go_parser.parse(temp_path)
            entities = go_parser.extract_entities(tree, temp_path)

            # Should have: FileEntity, ClassEntity (struct), 2x FunctionEntity (methods)
            entity_types = [e.__class__.__name__ for e in entities]
            assert "FileEntity" in entity_types
            assert "ClassEntity" in entity_types
            assert entity_types.count("FunctionEntity") == 2

            # Check struct name
            class_entities = [e for e in entities if e.__class__.__name__ == "ClassEntity"]
            assert len(class_entities) == 1
            assert class_entities[0].name == "Calculator"

            # Check method names
            func_entities = [e for e in entities if e.__class__.__name__ == "FunctionEntity"]
            func_names = {e.name for e in func_entities}
            assert func_names == {"Add", "GetValue"}
        finally:
            Path(temp_path).unlink()

    def test_extract_top_level_function(self, go_parser):
        """Test extraction of top-level functions."""
        source = '''package main

func Add(a, b int) int {
    return a + b
}

func Subtract(a, b int) int {
    return a - b
}
'''
        with tempfile.NamedTemporaryFile(suffix='.go', mode='w', delete=False) as f:
            f.write(source)
            temp_path = f.name

        try:
            tree = go_parser.parse(temp_path)
            entities = go_parser.extract_entities(tree, temp_path)

            func_entities = [e for e in entities if e.__class__.__name__ == "FunctionEntity"]
            func_names = {e.name for e in func_entities}
            assert func_names == {"Add", "Subtract"}

            # They should not be methods
            for func in func_entities:
                assert func.is_method is False
        finally:
            Path(temp_path).unlink()

    def test_extract_interface(self, go_parser):
        """Test interface extraction."""
        source = '''package main

type Reader interface {
    Read(p []byte) (n int, err error)
}
'''
        with tempfile.NamedTemporaryFile(suffix='.go', mode='w', delete=False) as f:
            f.write(source)
            temp_path = f.name

        try:
            tree = go_parser.parse(temp_path)
            entities = go_parser.extract_entities(tree, temp_path)

            # Interface should be extracted as ClassEntity
            class_entities = [e for e in entities if e.__class__.__name__ == "ClassEntity"]
            assert len(class_entities) == 1
            assert class_entities[0].name == "Reader"

            # Interface method should be extracted
            func_entities = [e for e in entities if e.__class__.__name__ == "FunctionEntity"]
            assert len(func_entities) == 1
            assert func_entities[0].name == "Read"
            assert func_entities[0].is_method is True
        finally:
            Path(temp_path).unlink()

    def test_extract_interface_with_multiple_methods(self, go_parser):
        """Test interface with multiple methods."""
        source = '''package main

type ReadWriter interface {
    Read(p []byte) (n int, err error)
    Write(p []byte) (n int, err error)
    Close() error
}
'''
        with tempfile.NamedTemporaryFile(suffix='.go', mode='w', delete=False) as f:
            f.write(source)
            temp_path = f.name

        try:
            tree = go_parser.parse(temp_path)
            entities = go_parser.extract_entities(tree, temp_path)

            func_entities = [e for e in entities if e.__class__.__name__ == "FunctionEntity"]
            func_names = {e.name for e in func_entities}
            assert func_names == {"Read", "Write", "Close"}
        finally:
            Path(temp_path).unlink()

    def test_method_with_pointer_receiver(self, go_parser):
        """Test method extraction with pointer receiver."""
        source = '''package main

type Counter struct {
    count int
}

func (c *Counter) Increment() {
    c.count++
}
'''
        with tempfile.NamedTemporaryFile(suffix='.go', mode='w', delete=False) as f:
            f.write(source)
            temp_path = f.name

        try:
            tree = go_parser.parse(temp_path)
            entities = go_parser.extract_entities(tree, temp_path)

            func_entities = [e for e in entities if e.__class__.__name__ == "FunctionEntity"]
            assert len(func_entities) == 1
            assert func_entities[0].name == "Increment"
            assert func_entities[0].is_method is True
        finally:
            Path(temp_path).unlink()

    def test_method_with_value_receiver(self, go_parser):
        """Test method extraction with value receiver."""
        source = '''package main

type Point struct {
    X, Y int
}

func (p Point) Distance() float64 {
    return 0.0
}
'''
        with tempfile.NamedTemporaryFile(suffix='.go', mode='w', delete=False) as f:
            f.write(source)
            temp_path = f.name

        try:
            tree = go_parser.parse(temp_path)
            entities = go_parser.extract_entities(tree, temp_path)

            func_entities = [e for e in entities if e.__class__.__name__ == "FunctionEntity"]
            assert len(func_entities) == 1
            assert func_entities[0].name == "Distance"
            assert func_entities[0].is_method is True
        finally:
            Path(temp_path).unlink()

    def test_extract_imports(self, go_parser):
        """Test import extraction."""
        source = '''package main

import (
    "fmt"
    "os"
    "encoding/json"
)

func main() {}
'''
        with tempfile.NamedTemporaryFile(suffix='.go', mode='w', delete=False) as f:
            f.write(source)
            temp_path = f.name

        try:
            tree = go_parser.parse(temp_path)
            entities = go_parser.extract_entities(tree, temp_path)
            relationships = go_parser.extract_relationships(tree, temp_path, entities)

            # Find IMPORTS relationships
            import_rels = [r for r in relationships if r.rel_type.value == "IMPORTS"]
            assert len(import_rels) >= 3

            # Check import targets
            import_targets = {r.target_id for r in import_rels}
            assert any("fmt" in t for t in import_targets)
            assert any("os" in t for t in import_targets)
            assert any("encoding/json" in t for t in import_targets)
        finally:
            Path(temp_path).unlink()

    def test_extract_single_import(self, go_parser):
        """Test single import extraction."""
        source = '''package main

import "fmt"

func main() {
    fmt.Println("hello")
}
'''
        with tempfile.NamedTemporaryFile(suffix='.go', mode='w', delete=False) as f:
            f.write(source)
            temp_path = f.name

        try:
            tree = go_parser.parse(temp_path)
            entities = go_parser.extract_entities(tree, temp_path)
            relationships = go_parser.extract_relationships(tree, temp_path, entities)

            import_rels = [r for r in relationships if r.rel_type.value == "IMPORTS"]
            assert len(import_rels) >= 1
            assert any("fmt" in r.target_id for r in import_rels)
        finally:
            Path(temp_path).unlink()

    def test_complexity_calculation(self, go_parser):
        """Test cyclomatic complexity calculation."""
        source = '''package main

func complexFunction(x int) string {
    if x > 0 {
        if x > 10 {
            return "big"
        } else {
            return "medium"
        }
    } else if x < 0 {
        return "negative"
    }
    return "zero"
}
'''
        with tempfile.NamedTemporaryFile(suffix='.go', mode='w', delete=False) as f:
            f.write(source)
            temp_path = f.name

        try:
            tree = go_parser.parse(temp_path)
            entities = go_parser.extract_entities(tree, temp_path)

            func_entities = [e for e in entities if e.__class__.__name__ == "FunctionEntity"]
            assert len(func_entities) == 1

            # Base complexity (1) + 3 if statements + 1 else
            assert func_entities[0].complexity >= 3
        finally:
            Path(temp_path).unlink()


class TestGoDocExtraction:
    """Tests for Go doc comment extraction."""

    @pytest.fixture
    def go_parser(self):
        """Create a Go parser."""
        from repotoire.parsers.tree_sitter_go import TreeSitterGoParser
        return TreeSitterGoParser()

    def test_doc_comment_on_function(self, go_parser):
        """Test doc comment extraction from function."""
        source = '''package main

// Add calculates the sum of two numbers.
// It returns the result as an integer.
func Add(a, b int) int {
    return a + b
}
'''
        with tempfile.NamedTemporaryFile(suffix='.go', mode='w', delete=False) as f:
            f.write(source)
            temp_path = f.name

        try:
            tree = go_parser.parse(temp_path)
            entities = go_parser.extract_entities(tree, temp_path)

            func = next((e for e in entities if e.name == 'Add'), None)
            assert func is not None
            assert func.docstring is not None
            assert "calculates the sum" in func.docstring
        finally:
            Path(temp_path).unlink()

    def test_doc_comment_on_struct(self, go_parser):
        """Test doc comment extraction from struct."""
        source = '''package main

// User represents a user in the system.
// It contains basic user information.
type User struct {
    Name string
}
'''
        with tempfile.NamedTemporaryFile(suffix='.go', mode='w', delete=False) as f:
            f.write(source)
            temp_path = f.name

        try:
            tree = go_parser.parse(temp_path)
            entities = go_parser.extract_entities(tree, temp_path)

            cls = next((e for e in entities if e.name == 'User'), None)
            assert cls is not None
            assert cls.docstring is not None
            assert "represents a user" in cls.docstring.lower()
        finally:
            Path(temp_path).unlink()

    def test_no_doc_comment_returns_none(self, go_parser):
        """Test that functions without doc comments return None for docstring."""
        source = '''package main

func noDoc(x int) int {
    return x * 2
}
'''
        with tempfile.NamedTemporaryFile(suffix='.go', mode='w', delete=False) as f:
            f.write(source)
            temp_path = f.name

        try:
            tree = go_parser.parse(temp_path)
            entities = go_parser.extract_entities(tree, temp_path)

            func = next((e for e in entities if e.name == 'noDoc'), None)
            assert func is not None
            assert func.docstring is None
        finally:
            Path(temp_path).unlink()


class TestAsyncDetection:
    """Tests for async/concurrent pattern detection."""

    @pytest.fixture
    def go_parser(self):
        """Create a Go parser."""
        from repotoire.parsers.tree_sitter_go import TreeSitterGoParser
        return TreeSitterGoParser()

    def test_channel_return_detected_as_async(self, go_parser):
        """Test that channel return type is detected as async."""
        source = '''package main

func fetchAsync() chan string {
    ch := make(chan string)
    go func() {
        ch <- "result"
    }()
    return ch
}
'''
        with tempfile.NamedTemporaryFile(suffix='.go', mode='w', delete=False) as f:
            f.write(source)
            temp_path = f.name

        try:
            tree = go_parser.parse(temp_path)
            entities = go_parser.extract_entities(tree, temp_path)

            func = next((e for e in entities if e.name == 'fetchAsync'), None)
            assert func is not None
            assert func.is_async is True
        finally:
            Path(temp_path).unlink()

    def test_goroutine_detected_as_async(self, go_parser):
        """Test that spawning goroutines is detected as async."""
        source = '''package main

func spawnWorkers() {
    for i := 0; i < 10; i++ {
        go worker(i)
    }
}

func worker(id int) {
    // do work
}
'''
        with tempfile.NamedTemporaryFile(suffix='.go', mode='w', delete=False) as f:
            f.write(source)
            temp_path = f.name

        try:
            tree = go_parser.parse(temp_path)
            entities = go_parser.extract_entities(tree, temp_path)

            func = next((e for e in entities if e.name == 'spawnWorkers'), None)
            assert func is not None
            assert func.is_async is True

            # worker itself doesn't spawn goroutines
            worker = next((e for e in entities if e.name == 'worker'), None)
            assert worker is not None
            assert worker.is_async is False
        finally:
            Path(temp_path).unlink()

    def test_regular_function_not_async(self, go_parser):
        """Test that regular functions are not detected as async."""
        source = '''package main

func syncFunction() int {
    return 42
}
'''
        with tempfile.NamedTemporaryFile(suffix='.go', mode='w', delete=False) as f:
            f.write(source)
            temp_path = f.name

        try:
            tree = go_parser.parse(temp_path)
            entities = go_parser.extract_entities(tree, temp_path)

            func = next((e for e in entities if e.name == 'syncFunction'), None)
            assert func is not None
            assert func.is_async is False
        finally:
            Path(temp_path).unlink()


class TestMethodCallExtraction:
    """Tests for method/function call extraction."""

    @pytest.fixture
    def go_parser(self):
        """Create a Go parser."""
        from repotoire.parsers.tree_sitter_go import TreeSitterGoParser
        return TreeSitterGoParser()

    def test_simple_function_call(self, go_parser):
        """Test simple function call extraction."""
        source = '''package main

import "fmt"

func main() {
    fmt.Println("hello")
    doSomething()
}

func doSomething() {
    // implementation
}
'''
        with tempfile.NamedTemporaryFile(suffix='.go', mode='w', delete=False) as f:
            f.write(source)
            temp_path = f.name

        try:
            tree = go_parser.parse(temp_path)
            entities = go_parser.extract_entities(tree, temp_path)
            relationships = go_parser.extract_relationships(tree, temp_path, entities)

            call_rels = [r for r in relationships if r.rel_type.value == "CALLS"]
            called_names = {r.target_id for r in call_rels}

            # Should detect Println or doSomething
            assert len(called_names) > 0
        finally:
            Path(temp_path).unlink()

    def test_method_call(self, go_parser):
        """Test method call extraction."""
        source = '''package main

type Service struct{}

func (s *Service) Start() {}
func (s *Service) Stop() {}

func main() {
    svc := &Service{}
    svc.Start()
    svc.Stop()
}
'''
        with tempfile.NamedTemporaryFile(suffix='.go', mode='w', delete=False) as f:
            f.write(source)
            temp_path = f.name

        try:
            tree = go_parser.parse(temp_path)
            entities = go_parser.extract_entities(tree, temp_path)
            relationships = go_parser.extract_relationships(tree, temp_path, entities)

            call_rels = [r for r in relationships if r.rel_type.value == "CALLS"]
            called_names = {r.target_id for r in call_rels}

            # Should detect Start and Stop calls
            assert any("Start" in n for n in called_names)
            assert any("Stop" in n for n in called_names)
        finally:
            Path(temp_path).unlink()


class TestReturnTypeDetection:
    """Tests for return statement detection."""

    @pytest.fixture
    def go_parser(self):
        """Create a Go parser."""
        from repotoire.parsers.tree_sitter_go import TreeSitterGoParser
        return TreeSitterGoParser()

    def test_function_with_return(self, go_parser):
        """Test function with return statement has_return is True."""
        source = '''package main

func withReturn(x int) int {
    return x * 2
}
'''
        with tempfile.NamedTemporaryFile(suffix='.go', mode='w', delete=False) as f:
            f.write(source)
            temp_path = f.name

        try:
            tree = go_parser.parse(temp_path)
            entities = go_parser.extract_entities(tree, temp_path)

            func = next((e for e in entities if e.name == 'withReturn'), None)
            assert func is not None
            assert func.has_return is True
        finally:
            Path(temp_path).unlink()

    def test_function_without_return(self, go_parser):
        """Test function without return statement has_return is False."""
        source = '''package main

import "fmt"

func noReturn() {
    fmt.Println("no return")
}
'''
        with tempfile.NamedTemporaryFile(suffix='.go', mode='w', delete=False) as f:
            f.write(source)
            temp_path = f.name

        try:
            tree = go_parser.parse(temp_path)
            entities = go_parser.extract_entities(tree, temp_path)

            func = next((e for e in entities if e.name == 'noReturn'), None)
            assert func is not None
            assert func.has_return is False
        finally:
            Path(temp_path).unlink()


class TestNestingLevelTracking:
    """Tests for nesting level calculation."""

    @pytest.fixture
    def go_parser(self):
        """Create a Go parser."""
        from repotoire.parsers.tree_sitter_go import TreeSitterGoParser
        return TreeSitterGoParser()

    def test_top_level_struct(self, go_parser):
        """Test that top-level struct has nesting level 0."""
        source = '''package main

type TopLevel struct {
    Name string
}
'''
        with tempfile.NamedTemporaryFile(suffix='.go', mode='w', delete=False) as f:
            f.write(source)
            temp_path = f.name

        try:
            tree = go_parser.parse(temp_path)
            entities = go_parser.extract_entities(tree, temp_path)

            cls = next((e for e in entities if e.name == 'TopLevel'), None)
            assert cls is not None
            assert cls.nesting_level == 0
        finally:
            Path(temp_path).unlink()

    def test_multiple_top_level_types(self, go_parser):
        """Test multiple top-level types all have nesting level 0."""
        source = '''package main

type First struct {
    Name string
}

type Second interface {
    Method()
}

type Third struct {
    Value int
}
'''
        with tempfile.NamedTemporaryFile(suffix='.go', mode='w', delete=False) as f:
            f.write(source)
            temp_path = f.name

        try:
            tree = go_parser.parse(temp_path)
            entities = go_parser.extract_entities(tree, temp_path)

            classes = [e for e in entities if e.__class__.__name__ == 'ClassEntity']
            assert len(classes) == 3

            for cls in classes:
                assert cls.nesting_level == 0, f"Type {cls.name} should have nesting level 0"
        finally:
            Path(temp_path).unlink()


class TestInterfaceEmbedding:
    """Tests for interface embedding extraction."""

    @pytest.fixture
    def go_parser(self):
        """Create a Go parser."""
        from repotoire.parsers.tree_sitter_go import TreeSitterGoParser
        return TreeSitterGoParser()

    def test_embedded_interface(self, go_parser):
        """Test that embedded interfaces are detected."""
        source = '''package main

type Reader interface {
    Read(p []byte) (n int, err error)
}

type Closer interface {
    Close() error
}

type ReadCloser interface {
    Reader
    Closer
}
'''
        with tempfile.NamedTemporaryFile(suffix='.go', mode='w', delete=False) as f:
            f.write(source)
            temp_path = f.name

        try:
            tree = go_parser.parse(temp_path)
            entities = go_parser.extract_entities(tree, temp_path)

            # Should have 3 interfaces
            class_entities = [e for e in entities if e.__class__.__name__ == "ClassEntity"]
            assert len(class_entities) == 3

            # ReadCloser should be extracted
            read_closer = next((e for e in class_entities if e.name == "ReadCloser"), None)
            assert read_closer is not None
        finally:
            Path(temp_path).unlink()


class TestSwitchComplexity:
    """Tests for switch statement complexity."""

    @pytest.fixture
    def go_parser(self):
        """Create a Go parser."""
        from repotoire.parsers.tree_sitter_go import TreeSitterGoParser
        return TreeSitterGoParser()

    def test_switch_complexity(self, go_parser):
        """Test that switch cases increase complexity."""
        source = '''package main

func withSwitch(x int) string {
    switch x {
    case 1:
        return "one"
    case 2:
        return "two"
    case 3:
        return "three"
    default:
        return "other"
    }
}
'''
        with tempfile.NamedTemporaryFile(suffix='.go', mode='w', delete=False) as f:
            f.write(source)
            temp_path = f.name

        try:
            tree = go_parser.parse(temp_path)
            entities = go_parser.extract_entities(tree, temp_path)

            func = next((e for e in entities if e.name == 'withSwitch'), None)
            assert func is not None
            # Base + switch + 4 cases
            assert func.complexity >= 4
        finally:
            Path(temp_path).unlink()


class TestForLoopComplexity:
    """Tests for for loop complexity."""

    @pytest.fixture
    def go_parser(self):
        """Create a Go parser."""
        from repotoire.parsers.tree_sitter_go import TreeSitterGoParser
        return TreeSitterGoParser()

    def test_for_loop_complexity(self, go_parser):
        """Test that for loops increase complexity."""
        source = '''package main

func withForLoop(items []int) int {
    sum := 0
    for i := 0; i < len(items); i++ {
        sum += items[i]
    }
    return sum
}
'''
        with tempfile.NamedTemporaryFile(suffix='.go', mode='w', delete=False) as f:
            f.write(source)
            temp_path = f.name

        try:
            tree = go_parser.parse(temp_path)
            entities = go_parser.extract_entities(tree, temp_path)

            func = next((e for e in entities if e.name == 'withForLoop'), None)
            assert func is not None
            # Base + for
            assert func.complexity >= 2
        finally:
            Path(temp_path).unlink()

    def test_range_loop_complexity(self, go_parser):
        """Test that range loops increase complexity."""
        source = '''package main

func withRangeLoop(items []int) int {
    sum := 0
    for _, v := range items {
        sum += v
    }
    return sum
}
'''
        with tempfile.NamedTemporaryFile(suffix='.go', mode='w', delete=False) as f:
            f.write(source)
            temp_path = f.name

        try:
            tree = go_parser.parse(temp_path)
            entities = go_parser.extract_entities(tree, temp_path)

            func = next((e for e in entities if e.name == 'withRangeLoop'), None)
            assert func is not None
            # Base + for/range
            assert func.complexity >= 2
        finally:
            Path(temp_path).unlink()

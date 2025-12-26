"""Unit tests for Rust-based function boundary extraction (REPO-245).

Tests the extract_function_boundaries and extract_function_boundaries_batch
functions from repotoire_fast which are used for training data extraction.
"""

import pytest

# Skip tests if Rust module not available
try:
    from repotoire_fast import (
        extract_function_boundaries,
        extract_function_boundaries_batch,
    )
    RUST_AVAILABLE = True
except ImportError:
    RUST_AVAILABLE = False


@pytest.mark.skipif(not RUST_AVAILABLE, reason="Rust module not available")
class TestExtractFunctionBoundaries:
    """Test extract_function_boundaries function."""

    def test_simple_function(self):
        """Test detection of a simple top-level function."""
        source = '''def hello():
    return "hello"
'''
        results = extract_function_boundaries(source)
        assert len(results) == 1
        name, start, end = results[0]
        assert name == "hello"
        assert start == 1
        assert end == 2

    def test_async_function(self):
        """Test detection of async function."""
        source = '''async def fetch_data():
    return await get_data()
'''
        results = extract_function_boundaries(source)
        assert len(results) == 1
        name, start, end = results[0]
        assert name == "fetch_data"
        assert start == 1
        assert end == 2

    def test_class_with_methods(self):
        """Test detection of class methods with class prefix."""
        source = '''class Greeter:
    def __init__(self, name):
        self.name = name

    def greet(self):
        return f"Hello, {self.name}"

    async def async_greet(self):
        return await self.greet()
'''
        results = extract_function_boundaries(source)
        assert len(results) == 3

        names = [r[0] for r in results]
        assert "Greeter.__init__" in names
        assert "Greeter.greet" in names
        assert "Greeter.async_greet" in names

    def test_nested_function(self):
        """Test detection of nested functions with correct prefix."""
        source = '''def outer():
    def inner():
        return "inner"
    return inner()
'''
        results = extract_function_boundaries(source)
        assert len(results) == 2

        names = [r[0] for r in results]
        assert "outer" in names
        assert "outer.inner" in names

    def test_deeply_nested_function(self):
        """Test detection of deeply nested functions."""
        source = '''def level1():
    def level2():
        def level3():
            return "deep"
        return level3()
    return level2()
'''
        results = extract_function_boundaries(source)
        assert len(results) == 3

        names = [r[0] for r in results]
        assert "level1" in names
        assert "level1.level2" in names
        assert "level1.level2.level3" in names

    def test_multiple_classes(self):
        """Test multiple classes with methods."""
        source = '''class Cat:
    def meow(self):
        return "meow"

class Dog:
    def bark(self):
        return "woof"
'''
        results = extract_function_boundaries(source)
        assert len(results) == 2

        names = [r[0] for r in results]
        assert "Cat.meow" in names
        assert "Dog.bark" in names

    def test_nested_class(self):
        """Test nested class with methods."""
        source = '''class Outer:
    class Inner:
        def inner_method(self):
            return "inner"

    def outer_method(self):
        return "outer"
'''
        results = extract_function_boundaries(source)
        assert len(results) == 2

        names = [r[0] for r in results]
        assert "Outer.Inner.inner_method" in names
        assert "Outer.outer_method" in names

    def test_method_with_nested_function(self):
        """Test class method containing a nested function."""
        source = '''class Helper:
    def process(self, data):
        def inner_processor(item):
            return item * 2
        return [inner_processor(d) for d in data]
'''
        results = extract_function_boundaries(source)
        assert len(results) == 2

        names = [r[0] for r in results]
        assert "Helper.process" in names
        assert "Helper.process.inner_processor" in names

    def test_empty_file(self):
        """Test empty file returns empty list."""
        source = ""
        results = extract_function_boundaries(source)
        assert results == []

    def test_no_functions(self):
        """Test file with no functions returns empty list."""
        source = '''x = 1
y = 2
z = x + y
'''
        results = extract_function_boundaries(source)
        assert results == []

    def test_syntax_error_graceful_degradation(self):
        """Test that syntax errors return empty list (graceful degradation)."""
        source = '''def broken(
    # Missing closing paren
'''
        results = extract_function_boundaries(source)
        assert results == []  # Graceful degradation, not an exception

    def test_function_in_if_block(self):
        """Test function defined inside if block."""
        source = '''if __name__ == "__main__":
    def main():
        print("Hello")
'''
        results = extract_function_boundaries(source)
        assert len(results) == 1
        assert results[0][0] == "main"

    def test_function_in_try_block(self):
        """Test function defined inside try block."""
        source = '''try:
    def risky():
        pass
except:
    def fallback():
        pass
finally:
    def cleanup():
        pass
'''
        results = extract_function_boundaries(source)
        assert len(results) == 3

        names = [r[0] for r in results]
        assert "risky" in names
        assert "fallback" in names
        assert "cleanup" in names

    def test_function_in_for_loop(self):
        """Test function defined inside for loop."""
        source = '''for i in range(3):
    def loop_func():
        return i
'''
        results = extract_function_boundaries(source)
        assert len(results) == 1
        assert results[0][0] == "loop_func"

    def test_function_in_while_loop(self):
        """Test function defined inside while loop."""
        source = '''while True:
    def while_func():
        pass
    break
'''
        results = extract_function_boundaries(source)
        assert len(results) == 1
        assert results[0][0] == "while_func"

    def test_function_in_with_block(self):
        """Test function defined inside with block."""
        source = '''with open("file.txt") as f:
    def process_file():
        return f.read()
'''
        results = extract_function_boundaries(source)
        assert len(results) == 1
        assert results[0][0] == "process_file"

    def test_async_with_and_for(self):
        """Test functions in async with and async for blocks."""
        source = '''async def main():
    async with get_resource() as r:
        def inner1():
            pass

    async for item in items():
        def inner2():
            pass
'''
        results = extract_function_boundaries(source)
        assert len(results) == 3

        names = [r[0] for r in results]
        assert "main" in names
        assert "main.inner1" in names
        assert "main.inner2" in names

    def test_complex_real_world_example(self):
        """Test a more realistic code snippet."""
        source = '''"""Module docstring."""

from typing import List, Optional

class DataProcessor:
    """Process data."""

    def __init__(self, config: dict):
        self.config = config
        self._cache = {}

    def process(self, data: List[dict]) -> List[dict]:
        """Process data items."""
        def validate_item(item):
            return item is not None

        results = []
        for item in data:
            if validate_item(item):
                results.append(self._transform(item))
        return results

    def _transform(self, item: dict) -> dict:
        return {"transformed": item}


def helper_function(x: int) -> int:
    """Module-level helper."""
    return x * 2


async def async_helper():
    """Async module-level helper."""
    return await fetch()
'''
        results = extract_function_boundaries(source)

        names = [r[0] for r in results]
        assert "DataProcessor.__init__" in names
        assert "DataProcessor.process" in names
        assert "DataProcessor.process.validate_item" in names
        assert "DataProcessor._transform" in names
        assert "helper_function" in names
        assert "async_helper" in names
        assert len(results) == 6

    def test_line_numbers_accuracy(self):
        """Test that line numbers are accurate."""
        source = '''# Comment line 1
# Comment line 2
def function_on_line_3():
    # Body line 4
    pass  # Line 5
'''
        results = extract_function_boundaries(source)
        assert len(results) == 1
        name, start, end = results[0]
        assert name == "function_on_line_3"
        assert start == 3
        assert end == 5

    def test_multiline_function_signature(self):
        """Test function with multiline signature."""
        source = '''def long_signature(
    arg1: int,
    arg2: str,
    arg3: float,
) -> dict:
    return {"a": arg1, "b": arg2, "c": arg3}
'''
        results = extract_function_boundaries(source)
        assert len(results) == 1
        name, start, end = results[0]
        assert name == "long_signature"
        assert start == 1
        assert end == 6

    def test_decorators(self):
        """Test that decorated functions are detected correctly."""
        source = '''@decorator
def decorated():
    pass

@classmethod
@another_decorator
def double_decorated():
    pass
'''
        results = extract_function_boundaries(source)
        assert len(results) == 2

        names = [r[0] for r in results]
        assert "decorated" in names
        assert "double_decorated" in names


@pytest.mark.skipif(not RUST_AVAILABLE, reason="Rust module not available")
class TestExtractFunctionBoundariesBatch:
    """Test extract_function_boundaries_batch function."""

    def test_batch_single_file(self):
        """Test batch processing with single file."""
        files = [
            ("src/a.py", '''def func_a():
    pass
''')
        ]
        results = extract_function_boundaries_batch(files)
        assert len(results) == 1

        path, boundaries = results[0]
        assert path == "src/a.py"
        assert len(boundaries) == 1
        assert boundaries[0][0] == "func_a"

    def test_batch_multiple_files(self):
        """Test batch processing with multiple files."""
        files = [
            ("src/a.py", '''def func_a():
    pass
'''),
            ("src/b.py", '''def func_b1():
    pass

def func_b2():
    pass
'''),
            ("src/c.py", '''class C:
    def method_c(self):
        pass
'''),
        ]
        results = extract_function_boundaries_batch(files)
        assert len(results) == 3

        # Convert to dict for easier assertions
        results_dict = {path: boundaries for path, boundaries in results}

        assert "src/a.py" in results_dict
        assert len(results_dict["src/a.py"]) == 1

        assert "src/b.py" in results_dict
        assert len(results_dict["src/b.py"]) == 2

        assert "src/c.py" in results_dict
        assert len(results_dict["src/c.py"]) == 1
        assert results_dict["src/c.py"][0][0] == "C.method_c"

    def test_batch_with_syntax_error(self):
        """Test batch processing handles syntax errors gracefully."""
        files = [
            ("good.py", '''def good():
    pass
'''),
            ("bad.py", '''def broken(
'''),  # Syntax error
            ("also_good.py", '''def also_good():
    pass
'''),
        ]
        results = extract_function_boundaries_batch(files)
        assert len(results) == 3  # All files processed

        results_dict = {path: boundaries for path, boundaries in results}

        assert len(results_dict["good.py"]) == 1
        assert len(results_dict["bad.py"]) == 0  # Empty due to syntax error
        assert len(results_dict["also_good.py"]) == 1

    def test_batch_empty_input(self):
        """Test batch processing with empty input."""
        results = extract_function_boundaries_batch([])
        assert results == []

    def test_batch_preserves_path(self):
        """Test that batch processing preserves exact file paths."""
        files = [
            ("path/with/multiple/segments/file.py", '''def f():
    pass
'''),
            ("another/path/module.py", '''def g():
    pass
'''),
        ]
        results = extract_function_boundaries_batch(files)

        paths = [path for path, _ in results]
        assert "path/with/multiple/segments/file.py" in paths
        assert "another/path/module.py" in paths


@pytest.mark.skipif(not RUST_AVAILABLE, reason="Rust module not available")
class TestFunctionBoundariesEdgeCases:
    """Edge cases and special scenarios."""

    def test_unicode_function_name(self):
        """Test function with unicode characters in name (if supported)."""
        # Python allows unicode identifiers since 3.0
        source = '''def grüß_gott():
    return "Hello"
'''
        results = extract_function_boundaries(source)
        assert len(results) == 1
        assert results[0][0] == "grüß_gott"

    def test_single_line_function(self):
        """Test single-line function definition."""
        source = '''def f(): pass
'''
        results = extract_function_boundaries(source)
        assert len(results) == 1
        name, start, end = results[0]
        assert name == "f"
        assert start == 1
        assert end == 1

    def test_function_with_only_docstring(self):
        """Test function that only has a docstring."""
        source = '''def documented():
    """This function does nothing."""
'''
        results = extract_function_boundaries(source)
        assert len(results) == 1

    def test_lambda_not_detected(self):
        """Test that lambda expressions are not detected as functions."""
        source = '''x = lambda a: a + 1
f = lambda: "hello"
'''
        results = extract_function_boundaries(source)
        assert results == []  # Lambdas are not function definitions

    def test_property_methods(self):
        """Test property getter/setter/deleter methods."""
        source = '''class Item:
    @property
    def value(self):
        return self._value

    @value.setter
    def value(self, v):
        self._value = v
'''
        results = extract_function_boundaries(source)
        assert len(results) == 2

        names = [r[0] for r in results]
        # Both should be named "Item.value" (same name, different decorators)
        assert names.count("Item.value") == 2

    def test_staticmethod_classmethod(self):
        """Test static and class methods."""
        source = '''class Utils:
    @staticmethod
    def static_func():
        pass

    @classmethod
    def class_func(cls):
        pass
'''
        results = extract_function_boundaries(source)
        assert len(results) == 2

        names = [r[0] for r in results]
        assert "Utils.static_func" in names
        assert "Utils.class_func" in names

    def test_dunder_methods(self):
        """Test detection of dunder methods."""
        source = '''class MyClass:
    def __init__(self):
        pass

    def __str__(self):
        return "MyClass"

    def __eq__(self, other):
        return True
'''
        results = extract_function_boundaries(source)
        assert len(results) == 3

        names = [r[0] for r in results]
        assert "MyClass.__init__" in names
        assert "MyClass.__str__" in names
        assert "MyClass.__eq__" in names

    def test_type_hints_preserved(self):
        """Test functions with complex type hints parse correctly."""
        source = '''from typing import Dict, List, Optional, Union

def complex_types(
    a: Dict[str, List[int]],
    b: Optional[Union[str, int]],
) -> List[Dict[str, Optional[int]]]:
    return []
'''
        results = extract_function_boundaries(source)
        assert len(results) == 1
        assert results[0][0] == "complex_types"

    def test_walrus_operator_in_function(self):
        """Test function containing walrus operator."""
        source = '''def with_walrus():
    if (n := len([1,2,3])) > 0:
        return n
'''
        results = extract_function_boundaries(source)
        assert len(results) == 1
        assert results[0][0] == "with_walrus"

    def test_match_statement(self):
        """Test function containing match statement (Python 3.10+)."""
        source = '''def with_match(x):
    match x:
        case 1:
            return "one"
        case _:
            return "other"
'''
        results = extract_function_boundaries(source)
        assert len(results) == 1
        assert results[0][0] == "with_match"

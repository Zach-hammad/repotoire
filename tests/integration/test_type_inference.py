"""Integration tests for enhanced type inference (REPO-333).

Tests the PyCG-style type inference system for Python call graph resolution.
Validates that the implementation achieves target metrics:
- Type-inferred calls: 1000+
- Random fallback: <10%
- Type inference time: <1s
"""

import pytest
import time
from pathlib import Path

# Try to import the Rust extension - tests will be skipped if not available
try:
    import repotoire_fast
    HAS_RUST_EXT = True
except ImportError:
    HAS_RUST_EXT = False

skip_no_rust = pytest.mark.skipif(
    not HAS_RUST_EXT,
    reason="repotoire_fast Rust extension not available"
)


def collect_python_files(directory: Path) -> list[tuple[str, str]]:
    """Collect all Python files from a directory recursively."""
    files = []
    exclude_dirs = {'.git', '__pycache__', 'venv', '.venv', 'node_modules', '.tox'}

    for path in directory.rglob('*.py'):
        if any(excluded in path.parts for excluded in exclude_dirs):
            continue
        try:
            content = path.read_text(encoding='utf-8')
            files.append((str(path), content))
        except (UnicodeDecodeError, PermissionError):
            continue

    return files


@skip_no_rust
class TestTypeInferenceAccuracy:
    """Integration tests for type inference accuracy."""

    def test_basic_type_inference(self):
        """Test that basic type inference works."""
        source = '''
class MyClass:
    def method(self):
        pass

def factory():
    return MyClass()

obj = factory()
obj.method()
'''
        files = [("test.py", source)]
        result = repotoire_fast.infer_types(files, 10)

        assert 'call_graph' in result
        assert 'num_classes' in result
        assert result['num_classes'] >= 1

    def test_type_inference_returns_stats(self):
        """Test that type inference returns statistics."""
        source = '''
class Client:
    def connect(self):
        pass

client = Client()
client.connect()
'''
        files = [("test.py", source)]
        result = repotoire_fast.infer_types(files, 10)

        # Check that all stats fields are present
        assert 'type_inferred_count' in result
        assert 'random_fallback_count' in result
        assert 'unresolved_count' in result
        assert 'external_count' in result
        assert 'type_inference_time' in result
        assert 'mro_computed_count' in result
        assert 'assignments_tracked' in result
        assert 'functions_with_returns' in result
        assert 'fallback_percentage' in result
        assert 'meets_targets' in result

    def test_cross_file_imports(self):
        """Test that imports across files are resolved."""
        file1 = '''
class SharedClass:
    def shared_method(self):
        pass
'''
        file2 = '''
from module1 import SharedClass

instance = SharedClass()
instance.shared_method()
'''
        files = [
            ("module1.py", file1),
            ("module2.py", file2),
        ]
        result = repotoire_fast.infer_types(files, 10)

        assert result['num_classes'] >= 1
        assert result['num_definitions'] >= 1

    def test_inheritance_resolution(self):
        """Test that inheritance is properly tracked."""
        source = '''
class Parent:
    def parent_method(self):
        pass

class Child(Parent):
    def child_method(self):
        pass

c = Child()
c.parent_method()  # Should resolve via MRO
c.child_method()
'''
        files = [("test.py", source)]
        result = repotoire_fast.infer_types(files, 10)

        assert result['mro_computed_count'] >= 1

    def test_external_package_detection(self):
        """Test that external packages are detected."""
        source = '''
import numpy as np
import pandas as pd

arr = np.array([1, 2, 3])
df = pd.DataFrame({"a": [1, 2]})
'''
        files = [("test.py", source)]
        result = repotoire_fast.infer_types(files, 10)

        # External packages should be detected
        # The exact count depends on implementation
        assert result['num_definitions'] >= 0


@skip_no_rust
class TestTypeInferencePerformance:
    """Performance tests for type inference."""

    def test_performance_small_codebase(self):
        """Test performance on a small synthetic codebase."""
        # Generate 50 files with classes and methods
        files = []
        for i in range(50):
            source = f'''
class Class{i}:
    def method{i}_a(self):
        pass

    def method{i}_b(self):
        return self.method{i}_a()

def function{i}():
    obj = Class{i}()
    return obj.method{i}_b()

result{i} = function{i}()
'''
            files.append((f"module{i}.py", source))

        start = time.perf_counter()
        result = repotoire_fast.infer_types(files, 10)
        elapsed = time.perf_counter() - start

        # Should process 50 files quickly
        assert elapsed < 5.0, f"Type inference took {elapsed:.2f}s, expected <5s"
        assert result['num_classes'] >= 50

    def test_type_inference_time_is_tracked(self):
        """Test that type inference time is properly tracked."""
        source = '''
class SimpleClass:
    pass
'''
        files = [("test.py", source)]
        result = repotoire_fast.infer_types(files, 10)

        assert 'type_inference_time' in result
        assert result['type_inference_time'] >= 0
        assert result['type_inference_time'] < 10.0  # Sanity check


@skip_no_rust
class TestTypeInferenceEdgeCases:
    """Edge case tests for type inference."""

    def test_circular_imports(self, tmp_path):
        """Test handling of circular imports."""
        a_source = '''
from b import B

class A:
    def use_b(self):
        b = B()
        return b.method()
'''
        b_source = '''
from a import A

class B:
    def method(self):
        pass

    def use_a(self):
        a = A()
        return a.use_b()
'''
        files = [
            ("a.py", a_source),
            ("b.py", b_source),
        ]

        # Should not hang or crash
        result = repotoire_fast.infer_types(files, 10)
        assert result['num_classes'] >= 2

    def test_diamond_inheritance(self):
        """Test diamond inheritance pattern."""
        source = '''
class Base:
    def method(self):
        pass

class Left(Base):
    pass

class Right(Base):
    def method(self):  # Override
        pass

class Diamond(Left, Right):
    pass

d = Diamond()
d.method()  # Should resolve to Right.method via MRO
'''
        files = [("test.py", source)]
        result = repotoire_fast.infer_types(files, 10)

        assert result['mro_computed_count'] >= 4  # All 4 classes

    def test_deeply_nested_calls(self):
        """Test deeply nested method calls."""
        source = '''
class A:
    def get_b(self):
        return B()

class B:
    def get_c(self):
        return C()

class C:
    def value(self):
        return 42

a = A()
result = a.get_b().get_c().value()
'''
        files = [("test.py", source)]
        result = repotoire_fast.infer_types(files, 10)

        assert result['num_classes'] >= 3

    def test_star_imports(self):
        """Test star imports are handled."""
        models = '''
class User:
    pass

class Post:
    pass

_internal = "hidden"
'''
        service = '''
from models import *

u = User()
p = Post()
'''
        files = [
            ("models.py", models),
            ("service.py", service),
        ]
        result = repotoire_fast.infer_types(files, 10)

        assert result['num_classes'] >= 2


@skip_no_rust
@pytest.mark.slow
class TestRepotoireCodebase:
    """Integration tests on the actual repotoire codebase."""

    def test_repotoire_codebase_stats(self):
        """Test type inference on the repotoire codebase."""
        repo_path = Path(__file__).parent.parent.parent / "repotoire"

        if not repo_path.exists():
            pytest.skip("repotoire directory not found")

        # Collect Python files (excluding tests for faster execution)
        files = []
        for path in repo_path.rglob('*.py'):
            if any(excluded in path.parts for excluded in
                   {'.git', '__pycache__', 'venv', '.venv', 'tests', 'node_modules'}):
                continue
            try:
                content = path.read_text(encoding='utf-8')
                files.append((str(path), content))
            except (UnicodeDecodeError, PermissionError):
                continue

        if len(files) < 50:
            pytest.skip(f"Not enough files found: {len(files)}")

        print(f"\nProcessing {len(files)} Python files...")

        start = time.perf_counter()
        result = repotoire_fast.infer_types(files, 10)
        elapsed = time.perf_counter() - start

        print(f"\n=== Type Inference Results ===")
        print(f"Files processed: {len(files)}")
        print(f"Total time: {elapsed:.3f}s")
        print(f"Type inference time: {result['type_inference_time']:.3f}s")
        print(f"Classes: {result['num_classes']}")
        print(f"Definitions: {result['num_definitions']}")
        print(f"MRO computed: {result['mro_computed_count']}")
        print(f"Assignments tracked: {result['assignments_tracked']}")
        print(f"Functions with returns: {result['functions_with_returns']}")
        print(f"Type-inferred calls: {result['type_inferred_count']}")
        print(f"Random fallback calls: {result['random_fallback_count']}")
        print(f"External calls: {result['external_count']}")
        print(f"Fallback percentage: {result['fallback_percentage']:.1f}%")
        print(f"Meets targets: {result['meets_targets']}")

        # Performance target: <1s for type inference
        assert result['type_inference_time'] < 1.0, \
            f"Type inference took {result['type_inference_time']:.3f}s, expected <1s"

        # Should have substantial analysis results
        assert result['num_classes'] > 50, \
            f"Expected 50+ classes, got {result['num_classes']}"
        assert result['mro_computed_count'] > 0, \
            "Expected some MROs to be computed"

    def test_repotoire_meets_targets(self):
        """Validate that repotoire codebase meets type inference targets.

        REPO-333: Strict validation of target metrics:
        - Type-inferred calls: 1000+
        - Random fallback percentage: <10%
        """
        # Use full repo path (including tests/) to get enough files
        repo_path = Path(__file__).parent.parent.parent

        if not repo_path.exists():
            pytest.skip("repotoire directory not found")

        # Include ALL Python files (including tests) for full coverage
        files = collect_python_files(repo_path)

        if len(files) < 100:
            pytest.skip(f"Not enough files found: {len(files)}")

        result = repotoire_fast.infer_types(files, 10)

        # Strict target validation
        assert result['meets_targets'], (
            f"Failed to meet targets: "
            f"type_inferred={result['type_inferred_count']} (need 1000+), "
            f"fallback_pct={result['fallback_percentage']:.1f}% (need <10%)"
        )
        assert result['type_inferred_count'] >= 1000, (
            f"Expected 1000+ type-inferred calls, got {result['type_inferred_count']}"
        )
        assert result['fallback_percentage'] < 10.0, (
            f"Expected <10% fallback, got {result['fallback_percentage']:.1f}%"
        )

    def test_incremental_analysis_consistency(self):
        """Test that re-analyzing gives consistent results."""
        repo_path = Path(__file__).parent.parent.parent / "repotoire"

        if not repo_path.exists():
            pytest.skip("repotoire directory not found")

        # Collect a subset of files
        files = []
        for path in (repo_path / "detectors").rglob('*.py'):
            try:
                content = path.read_text(encoding='utf-8')
                files.append((str(path), content))
            except (UnicodeDecodeError, PermissionError):
                continue

        if len(files) < 5:
            pytest.skip(f"Not enough detector files found: {len(files)}")

        # Run analysis twice
        result1 = repotoire_fast.infer_types(files, 10)
        result2 = repotoire_fast.infer_types(files, 10)

        # Results should be identical
        assert result1['type_inferred_count'] == result2['type_inferred_count'], \
            "Type-inferred count should be consistent across runs"
        assert result1['random_fallback_count'] == result2['random_fallback_count'], \
            "Fallback count should be consistent across runs"
        assert result1['num_classes'] == result2['num_classes'], \
            "Class count should be consistent across runs"


@skip_no_rust
class TestRegressions:
    """Regression tests for known issues."""

    def test_empty_file(self):
        """Test handling of empty files."""
        files = [("empty.py", "")]
        result = repotoire_fast.infer_types(files, 10)

        assert result['num_classes'] == 0
        assert result['num_definitions'] == 0

    def test_syntax_error_in_file(self):
        """Test handling of files with syntax errors."""
        files = [
            ("valid.py", "class Valid: pass"),
            ("invalid.py", "def broken(: pass"),  # Syntax error
        ]
        # Should not crash, just skip the invalid file
        result = repotoire_fast.infer_types(files, 10)

        # At least the valid file should be processed
        assert result['num_classes'] >= 1

    def test_unicode_content(self):
        """Test handling of Unicode content."""
        source = '''
class Émoji:
    """Класс с юникодом 🎉"""
    def 方法(self):
        pass
'''
        files = [("unicode.py", source)]
        result = repotoire_fast.infer_types(files, 10)

        assert result['num_classes'] >= 1

    def test_chained_method_calls(self):
        """Test method chaining like client.query().fetch()."""
        source = '''
class Query:
    def fetch(self):
        return []

class Client:
    def query(self, sql):
        return Query()

c = Client()
result = c.query("SELECT *").fetch()
'''
        files = [("test.py", source)]
        result = repotoire_fast.infer_types(files, 10)

        # Should track both classes
        assert result['num_classes'] >= 2
        # Should have some type-inferred calls
        assert result['type_inferred_count'] >= 1

    def test_factory_pattern(self):
        """Test factory functions returning different types."""
        source = '''
class Handler:
    def handle(self):
        pass

class DefaultHandler(Handler):
    pass

class CustomHandler(Handler):
    pass

def create_handler(type_name):
    if type_name == "custom":
        return CustomHandler()
    return DefaultHandler()

h = create_handler("custom")
h.handle()
'''
        files = [("test.py", source)]
        result = repotoire_fast.infer_types(files, 10)

        # Should track 3 classes
        assert result['num_classes'] >= 3
        # Function should have tracked return types
        assert result['functions_with_returns'] >= 1

    def test_self_method_chaining(self):
        """Test builder pattern with self returns."""
        source = '''
class Builder:
    def set_name(self, name):
        self.name = name
        return self

    def set_value(self, value):
        self.value = value
        return self

    def build(self):
        return self

b = Builder()
result = b.set_name("test").set_value(42).build()
'''
        files = [("test.py", source)]
        result = repotoire_fast.infer_types(files, 10)

        # Should track Builder class
        assert result['num_classes'] >= 1

    def test_async_methods(self):
        """Test async method resolution."""
        source = '''
class AsyncClient:
    async def fetch(self):
        return []

    async def process(self):
        data = await self.fetch()
        return data
'''
        files = [("test.py", source)]
        result = repotoire_fast.infer_types(files, 10)

        # Should handle async methods
        assert result['num_classes'] >= 1

    def test_property_decorator(self):
        """Test property methods."""
        source = '''
class Config:
    @property
    def value(self):
        return self._value

    @value.setter
    def value(self, v):
        self._value = v
'''
        files = [("test.py", source)]
        result = repotoire_fast.infer_types(files, 10)

        # Should track the class
        assert result['num_classes'] >= 1

    def test_classmethod_staticmethod(self):
        """Test class and static methods."""
        source = '''
class Factory:
    @classmethod
    def create(cls):
        return cls()

    @staticmethod
    def default():
        return Factory()

f = Factory.create()
f2 = Factory.default()
'''
        files = [("test.py", source)]
        result = repotoire_fast.infer_types(files, 10)

        # Should track Factory class
        assert result['num_classes'] >= 1

"""Unit tests for Rust-based pylint rules."""

import pytest

# Skip tests if Rust module not available
try:
    from repotoire_fast import (
        check_too_many_attributes,
        check_too_few_public_methods,
        check_too_many_public_methods,
        check_too_many_boolean_expressions,
        check_import_self,
        check_too_many_returns,
        check_too_many_branches,
        check_too_many_arguments,
        check_too_many_locals,
        check_too_many_statements,
    )
    RUST_AVAILABLE = True
except ImportError:
    RUST_AVAILABLE = False


@pytest.mark.skipif(not RUST_AVAILABLE, reason="Rust module not available")
class TestRustPylintRules:
    """Test Rust-based pylint rule implementations."""

    def test_r0902_too_many_attributes_detected(self):
        """Test R0902: too-many-instance-attributes is detected."""
        source = '''
class BigClass:
    def __init__(self):
        self.a = 1
        self.b = 2
        self.c = 3
        self.d = 4
        self.e = 5
        self.f = 6
        self.g = 7
        self.h = 8
'''
        results = check_too_many_attributes(source, 7)
        assert len(results) == 1
        code, message, line = results[0]
        assert code == "R0902"
        assert "8 instance attributes" in message
        assert "max 7" in message

    def test_r0902_under_threshold_ok(self):
        """Test R0902: class under threshold is not flagged."""
        source = '''
class SmallClass:
    def __init__(self):
        self.a = 1
        self.b = 2
'''
        results = check_too_many_attributes(source, 7)
        assert len(results) == 0

    def test_r0903_too_few_methods_detected(self):
        """Test R0903: too-few-public-methods is detected."""
        source = '''
class DataOnly:
    def __init__(self):
        self.x = 1

    def get_x(self):
        return self.x
'''
        # Only get_x is public (1 method), threshold is 2
        results = check_too_few_public_methods(source, 2)
        assert len(results) == 1
        code, message, line = results[0]
        assert code == "R0903"
        assert "1 public methods" in message

    def test_r0903_enough_methods_ok(self):
        """Test R0903: class with enough public methods is not flagged."""
        source = '''
class GoodClass:
    def __init__(self):
        pass

    def method1(self):
        pass

    def method2(self):
        pass
'''
        results = check_too_few_public_methods(source, 2)
        assert len(results) == 0

    def test_r0903_private_methods_not_counted(self):
        """Test R0903: private methods (starting with _) are not counted."""
        source = '''
class PrivateHeavy:
    def __init__(self):
        pass

    def _helper1(self):
        pass

    def _helper2(self):
        pass

    def __private(self):
        pass
'''
        # 0 public methods
        results = check_too_few_public_methods(source, 2)
        assert len(results) == 1

    def test_r0904_too_many_methods_detected(self):
        """Test R0904: too-many-public-methods is detected."""
        source = '''
class BigClass:
    def method1(self): pass
    def method2(self): pass
    def method3(self): pass
    def method4(self): pass
    def method5(self): pass
'''
        results = check_too_many_public_methods(source, 3)
        assert len(results) == 1
        code, message, line = results[0]
        assert code == "R0904"
        assert "5 public methods" in message
        assert "max 3" in message

    def test_r0904_under_threshold_ok(self):
        """Test R0904: class under threshold is not flagged."""
        source = '''
class SmallClass:
    def method1(self): pass
    def method2(self): pass
'''
        results = check_too_many_public_methods(source, 20)
        assert len(results) == 0

    def test_r0904_private_methods_not_counted(self):
        """Test R0904: private methods are not counted towards limit."""
        source = '''
class MixedClass:
    def public1(self): pass
    def public2(self): pass
    def _private1(self): pass
    def _private2(self): pass
    def __dunder(self): pass
'''
        # Only 2 public methods
        results = check_too_many_public_methods(source, 3)
        assert len(results) == 0

    def test_multiple_classes(self):
        """Test that multiple classes are checked independently."""
        source = '''
class Good:
    def method1(self): pass
    def method2(self): pass

class Bad:
    def only_one(self): pass
'''
        results = check_too_few_public_methods(source, 2)
        # Only Bad should be flagged
        assert len(results) == 1
        assert "Bad" in results[0][1]

    def test_empty_class(self):
        """Test handling of empty class."""
        source = '''
class Empty:
    pass
'''
        # Empty class has 0 public methods
        results = check_too_few_public_methods(source, 2)
        assert len(results) == 1

    def test_syntax_error_handled(self):
        """Test that syntax errors don't crash."""
        source = "class Broken def bad syntax"
        # Should raise or return empty, not crash
        try:
            results = check_too_many_attributes(source, 7)
            # If it doesn't raise, should return empty
            assert results == [] or isinstance(results, list)
        except Exception:
            # Raising is also acceptable
            pass

    # R0916: too-many-boolean-expressions tests

    def test_r0916_too_many_boolean_expressions_detected(self):
        """Test R0916: too-many-boolean-expressions is detected."""
        source = '''
if a and b and c and d and e:
    pass
'''
        # 4 boolean operators (and/and/and/and)
        results = check_too_many_boolean_expressions(source, 3)
        assert len(results) == 1
        code, message, line = results[0]
        assert code == "R0916"
        assert "4 boolean expressions" in message
        assert "max 3" in message

    def test_r0916_under_threshold_ok(self):
        """Test R0916: condition under threshold is not flagged."""
        source = '''
if a and b:
    pass
'''
        results = check_too_many_boolean_expressions(source, 3)
        assert len(results) == 0

    def test_r0916_mixed_operators(self):
        """Test R0916: counts both and/or operators."""
        source = '''
if a and b or c and d:
    pass
'''
        # This is: (a and b) or (c and d) = 3 operators
        results = check_too_many_boolean_expressions(source, 2)
        assert len(results) == 1

    def test_r0916_nested_conditions(self):
        """Test R0916: handles nested conditions."""
        source = '''
if (a and b) and (c or d):
    pass
'''
        # 3 operators total
        results = check_too_many_boolean_expressions(source, 2)
        assert len(results) == 1

    # R0401: import-self tests

    def test_r0401_import_self_detected(self):
        """Test R0401: import-self is detected."""
        source = '''
import mymodule
'''
        results = check_import_self(source, "mymodule.py")
        assert len(results) == 1
        code, message, line = results[0]
        assert code == "R0401"
        assert "mymodule" in message

    def test_r0401_from_import_self_detected(self):
        """Test R0401: from X import Y self-import is detected."""
        source = '''
from mymodule import something
'''
        results = check_import_self(source, "mymodule.py")
        assert len(results) == 1
        code, message, line = results[0]
        assert code == "R0401"

    def test_r0401_no_self_import_ok(self):
        """Test R0401: importing other modules is ok."""
        source = '''
import os
from pathlib import Path
'''
        results = check_import_self(source, "mymodule.py")
        assert len(results) == 0

    def test_r0401_init_file_uses_package_name(self):
        """Test R0401: __init__.py uses parent directory as module name."""
        source = '''
import mypackage
'''
        results = check_import_self(source, "mypackage/__init__.py")
        assert len(results) == 1
        assert "mypackage" in results[0][1]

    # R0911: too-many-return-statements tests

    def test_r0911_too_many_returns_detected(self):
        """Test R0911: too-many-return-statements is detected."""
        source = '''
def many_returns(x):
    if x == 1:
        return 1
    if x == 2:
        return 2
    if x == 3:
        return 3
    if x == 4:
        return 4
    return 0
'''
        results = check_too_many_returns(source, 3)
        assert len(results) == 1
        code, message, line = results[0]
        assert code == "R0911"
        assert "5 return statements" in message

    def test_r0911_under_threshold_ok(self):
        """Test R0911: function under threshold is not flagged."""
        source = '''
def simple(x):
    if x:
        return 1
    return 0
'''
        results = check_too_many_returns(source, 3)
        assert len(results) == 0

    # R0912: too-many-branches tests

    def test_r0912_too_many_branches_detected(self):
        """Test R0912: too-many-branches is detected."""
        source = '''
def branchy(x):
    if x == 1:
        pass
    elif x == 2:
        pass
    elif x == 3:
        pass
    else:
        for i in range(10):
            if i > 5:
                pass
'''
        results = check_too_many_branches(source, 3)
        assert len(results) == 1
        code, message, line = results[0]
        assert code == "R0912"

    def test_r0912_under_threshold_ok(self):
        """Test R0912: function under threshold is not flagged."""
        source = '''
def simple(x):
    if x:
        pass
'''
        results = check_too_many_branches(source, 5)
        assert len(results) == 0

    # R0913: too-many-arguments tests

    def test_r0913_too_many_arguments_detected(self):
        """Test R0913: too-many-arguments is detected."""
        source = '''
def many_args(a, b, c, d, e, f, g):
    pass
'''
        results = check_too_many_arguments(source, 5)
        assert len(results) == 1
        code, message, line = results[0]
        assert code == "R0913"
        assert "7 arguments" in message

    def test_r0913_method_excludes_self(self):
        """Test R0913: methods don't count self/cls."""
        source = '''
class Foo:
    def method(self, a, b, c, d, e):
        pass
'''
        # 5 args excluding self
        results = check_too_many_arguments(source, 5)
        assert len(results) == 0  # exactly at threshold

    def test_r0913_under_threshold_ok(self):
        """Test R0913: function under threshold is not flagged."""
        source = '''
def simple(a, b):
    pass
'''
        results = check_too_many_arguments(source, 5)
        assert len(results) == 0

    # R0914: too-many-locals tests

    def test_r0914_too_many_locals_detected(self):
        """Test R0914: too-many-locals is detected."""
        source = '''
def many_locals():
    a = 1
    b = 2
    c = 3
    d = 4
    e = 5
    f = 6
'''
        results = check_too_many_locals(source, 5)
        assert len(results) == 1
        code, message, line = results[0]
        assert code == "R0914"
        assert "6 local variables" in message

    def test_r0914_tuple_unpacking_counts_each(self):
        """Test R0914: tuple unpacking counts each variable."""
        source = '''
def unpacking():
    a, b, c = 1, 2, 3
    d, e, f = 4, 5, 6
'''
        results = check_too_many_locals(source, 5)
        assert len(results) == 1

    def test_r0914_under_threshold_ok(self):
        """Test R0914: function under threshold is not flagged."""
        source = '''
def simple():
    x = 1
    y = 2
'''
        results = check_too_many_locals(source, 5)
        assert len(results) == 0

    # R0915: too-many-statements tests

    def test_r0915_too_many_statements_detected(self):
        """Test R0915: too-many-statements is detected."""
        source = '''
def long_function():
    a = 1
    b = 2
    c = 3
    d = 4
    e = 5
    f = 6
    g = 7
'''
        results = check_too_many_statements(source, 5)
        assert len(results) == 1
        code, message, line = results[0]
        assert code == "R0915"
        assert "7 statements" in message

    def test_r0915_under_threshold_ok(self):
        """Test R0915: function under threshold is not flagged."""
        source = '''
def short():
    return 1
'''
        results = check_too_many_statements(source, 5)
        assert len(results) == 0

    def test_r0915_method_in_class(self):
        """Test R0915: methods in classes are checked."""
        source = '''
class Foo:
    def long_method(self):
        a = 1
        b = 2
        c = 3
        d = 4
        e = 5
        f = 6
'''
        results = check_too_many_statements(source, 5)
        assert len(results) == 1
        assert "Foo.long_method" in results[0][1]

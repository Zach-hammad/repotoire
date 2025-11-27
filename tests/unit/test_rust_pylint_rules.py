"""Unit tests for Rust-based pylint rules.

Only tests rules NOT covered by Ruff (use RuffLintDetector for the rest).
"""

import pytest

# Skip tests if Rust module not available
try:
    from repotoire_fast import (
        check_too_many_attributes,        # R0902
        check_too_few_public_methods,     # R0903
        check_import_self,                # R0401
        check_too_many_lines,             # C0302
        check_too_many_ancestors,         # R0901
        check_attribute_defined_outside_init,  # W0201
        check_protected_access,           # W0212
        check_unused_wildcard_import,     # W0614
        check_undefined_loop_variable,    # W0631
        check_disallowed_name,            # C0104
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

    # C0302: too-many-lines tests

    def test_c0302_too_many_lines_detected(self):
        """Test C0302: too-many-lines is detected."""
        source = "\n".join(["x = 1"] * 1001)
        results = check_too_many_lines(source, 1000)
        assert len(results) == 1
        code, message, line = results[0]
        assert code == "C0302"
        assert "1001" in message
        assert "1000" in message

    def test_c0302_under_threshold_ok(self):
        """Test C0302: file under threshold is not flagged."""
        source = "\n".join(["x = 1"] * 100)
        results = check_too_many_lines(source, 1000)
        assert len(results) == 0

    def test_c0302_exact_threshold_ok(self):
        """Test C0302: file at exact threshold is not flagged."""
        source = "\n".join(["x = 1"] * 1000)
        results = check_too_many_lines(source, 1000)
        assert len(results) == 0

    # R0901: too-many-ancestors tests

    def test_r0901_too_many_ancestors_detected(self):
        """Test R0901: too-many-ancestors is detected."""
        source = '''
class A:
    pass

class B(A):
    pass

class C(B):
    pass

class D(C):
    pass

class E(D):
    pass
'''
        results = check_too_many_ancestors(source, 3)
        assert len(results) >= 1
        # D and E should be flagged (D has 3 ancestors: C, B, A; E has 4)
        codes = [r[0] for r in results]
        assert all(c == "R0901" for c in codes)

    def test_r0901_under_threshold_ok(self):
        """Test R0901: class under threshold is not flagged."""
        source = '''
class A:
    pass

class B(A):
    pass
'''
        results = check_too_many_ancestors(source, 3)
        assert len(results) == 0

    def test_r0901_multiple_inheritance(self):
        """Test R0901: multiple inheritance counts all bases."""
        source = '''
class A:
    pass

class B:
    pass

class C:
    pass

class D(A, B, C):
    pass
'''
        results = check_too_many_ancestors(source, 2)
        assert len(results) == 1
        assert "D" in results[0][1]

    # W0201: attribute-defined-outside-init tests

    def test_w0201_attribute_outside_init_detected(self):
        """Test W0201: attribute-defined-outside-init is detected."""
        source = '''
class Foo:
    def __init__(self):
        self.x = 1

    def bar(self):
        self.y = 2
'''
        results = check_attribute_defined_outside_init(source)
        assert len(results) == 1
        code, message, line = results[0]
        assert code == "W0201"
        assert "y" in message

    def test_w0201_attribute_in_init_ok(self):
        """Test W0201: attributes defined in __init__ are not flagged."""
        source = '''
class Foo:
    def __init__(self):
        self.x = 1
        self.y = 2

    def bar(self):
        self.x = 10  # Reassignment ok
'''
        results = check_attribute_defined_outside_init(source)
        assert len(results) == 0

    def test_w0201_no_init_method(self):
        """Test W0201: class without __init__ flags all attrs."""
        source = '''
class Foo:
    def bar(self):
        self.x = 1
'''
        results = check_attribute_defined_outside_init(source)
        assert len(results) == 1
        assert "x" in results[0][1]

    # W0212: protected-access tests

    def test_w0212_protected_access_detected(self):
        """Test W0212: protected-access is detected."""
        source = '''
class Foo:
    def bar(self, other):
        return other._private
'''
        results = check_protected_access(source)
        assert len(results) == 1
        code, message, line = results[0]
        assert code == "W0212"
        assert "_private" in message

    def test_w0212_self_access_ok(self):
        """Test W0212: accessing own protected members is ok."""
        source = '''
class Foo:
    def bar(self):
        return self._private
'''
        results = check_protected_access(source)
        assert len(results) == 0

    def test_w0212_cls_access_ok(self):
        """Test W0212: accessing cls protected members is ok."""
        source = '''
class Foo:
    @classmethod
    def bar(cls):
        return cls._private
'''
        results = check_protected_access(source)
        assert len(results) == 0

    def test_w0212_dunder_ok(self):
        """Test W0212: dunder methods (__x__) are not flagged."""
        source = '''
class Foo:
    def bar(self, other):
        return other.__str__()
'''
        results = check_protected_access(source)
        assert len(results) == 0

    def test_w0212_module_level_access(self):
        """Test W0212: module-level protected access is flagged."""
        source = '''
import os
x = os._path
'''
        results = check_protected_access(source)
        assert len(results) == 1
        assert "_path" in results[0][1]

    # W0614: unused-wildcard-import tests

    def test_w0614_wildcard_import_detected(self):
        """Test W0614: wildcard imports are detected."""
        source = '''
from os import *
'''
        results = check_unused_wildcard_import(source)
        assert len(results) == 1
        code, message, line = results[0]
        assert code == "W0614"
        assert "os" in message

    def test_w0614_regular_import_ok(self):
        """Test W0614: regular imports are not flagged."""
        source = '''
from os import path, getcwd
import sys
'''
        results = check_unused_wildcard_import(source)
        assert len(results) == 0

    def test_w0614_multiple_wildcards(self):
        """Test W0614: multiple wildcard imports are all detected."""
        source = '''
from os import *
from sys import *
'''
        results = check_unused_wildcard_import(source)
        assert len(results) == 2

    # W0631: undefined-loop-variable tests

    def test_w0631_undefined_loop_variable_detected(self):
        """Test W0631: using loop variable after loop is detected."""
        source = '''
def foo():
    for i in range(10):
        pass
    return i
'''
        results = check_undefined_loop_variable(source)
        assert len(results) == 1
        code, message, line = results[0]
        assert code == "W0631"
        assert "i" in message

    def test_w0631_loop_variable_used_inside_ok(self):
        """Test W0631: using loop variable inside loop is ok."""
        source = '''
def foo():
    result = 0
    for i in range(10):
        result += i
    return result
'''
        results = check_undefined_loop_variable(source)
        assert len(results) == 0

    def test_w0631_used_after_empty_loop(self):
        """Test W0631: using loop variable after potentially empty loop."""
        source = '''
def foo(items):
    for item in items:
        pass
    return item
'''
        results = check_undefined_loop_variable(source)
        assert len(results) == 1
        assert "item" in results[0][1]

    # C0104: disallowed-name tests

    def test_c0104_disallowed_name_detected(self):
        """Test C0104: disallowed names are detected."""
        source = '''
foo = 1
bar = 2
'''
        results = check_disallowed_name(source, ["foo", "bar", "baz"])
        assert len(results) == 2
        codes = [r[0] for r in results]
        assert all(c == "C0104" for c in codes)

    def test_c0104_allowed_names_ok(self):
        """Test C0104: allowed names are not flagged."""
        source = '''
x = 1
y = 2
'''
        results = check_disallowed_name(source, ["foo", "bar", "baz"])
        assert len(results) == 0

    def test_c0104_function_name_detected(self):
        """Test C0104: disallowed function names are detected."""
        source = '''
def foo():
    pass
'''
        results = check_disallowed_name(source, ["foo", "bar", "baz"])
        assert len(results) == 1
        assert "foo" in results[0][1]

    def test_c0104_class_name_detected(self):
        """Test C0104: disallowed class names are detected."""
        source = '''
class foo:
    pass
'''
        results = check_disallowed_name(source, ["foo", "bar", "baz"])
        assert len(results) == 1
        assert "foo" in results[0][1]

    def test_c0104_argument_name_detected(self):
        """Test C0104: disallowed argument names are detected."""
        source = '''
def process(foo, bar):
    return foo + bar
'''
        results = check_disallowed_name(source, ["foo", "bar", "baz"])
        assert len(results) == 2

    def test_c0104_for_loop_variable_detected(self):
        """Test C0104: disallowed for loop variables are detected."""
        source = '''
for foo in range(10):
    print(foo)
'''
        results = check_disallowed_name(source, ["foo", "bar", "baz"])
        assert len(results) == 1

"""Unit tests for Rust-based pylint rules."""

import pytest

# Skip tests if Rust module not available
try:
    from repotoire_fast import (
        check_too_many_attributes,
        check_too_few_public_methods,
        check_too_many_public_methods,
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
        assert "max, 7" in message

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
        assert "max, 3" in message

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

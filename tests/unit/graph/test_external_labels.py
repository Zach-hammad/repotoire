"""Tests for external_labels module (KG-1 fix)."""

import pytest
from repotoire.graph.external_labels import (
    get_external_node_label,
    is_likely_external_reference,
    PYTHON_BUILTINS,
)


class TestGetExternalNodeLabel:
    """Test get_external_node_label function."""

    def test_python_builtins_return_builtin_function(self):
        """Python builtins should be labeled as BuiltinFunction."""
        for builtin in ['len', 'str', 'print', 'isinstance', 'range', 'sorted']:
            assert get_external_node_label(builtin) == "BuiltinFunction"

    def test_uppercase_names_return_external_class(self):
        """Names starting with uppercase should be labeled as ExternalClass."""
        assert get_external_node_label("Path") == "ExternalClass"
        assert get_external_node_label("DataFrame") == "ExternalClass"
        assert get_external_node_label("MyClass") == "ExternalClass"

    def test_lowercase_names_return_external_function(self):
        """Unknown lowercase names should be labeled as ExternalFunction."""
        assert get_external_node_label("some_func") == "ExternalFunction"
        assert get_external_node_label("my_utility") == "ExternalFunction"

    def test_qualified_name_from_stdlib(self):
        """Qualified names from stdlib should be properly labeled."""
        assert get_external_node_label("Path", "pathlib.Path") == "ExternalClass"
        assert get_external_node_label("join", "os.path.join") == "ExternalFunction"

    def test_builtins_set_is_complete(self):
        """Verify common builtins are in the set."""
        common_builtins = [
            'len', 'str', 'int', 'float', 'bool', 'list', 'dict', 'set', 'tuple',
            'print', 'range', 'enumerate', 'zip', 'map', 'filter', 'sorted',
            'isinstance', 'type', 'super', 'property', 'staticmethod', 'classmethod',
            'abs', 'all', 'any', 'min', 'max', 'sum', 'round',
        ]
        for builtin in common_builtins:
            assert builtin in PYTHON_BUILTINS, f"{builtin} should be in PYTHON_BUILTINS"


class TestIsLikelyExternalReference:
    """Test is_likely_external_reference function."""

    def test_internal_qualified_names(self):
        """Internal qualified names with :: should not be external."""
        assert not is_likely_external_reference("file.py::MyClass.method:123")
        assert not is_likely_external_reference("module.py::function:45")

    def test_external_simple_names(self):
        """Simple names without :: are likely external."""
        assert is_likely_external_reference("len")
        assert is_likely_external_reference("print")
        assert is_likely_external_reference("Path")

    def test_external_stdlib_modules(self):
        """Names starting with stdlib modules are external."""
        assert is_likely_external_reference("pathlib.Path")
        assert is_likely_external_reference("os.path.join")
        assert is_likely_external_reference("json.dumps")

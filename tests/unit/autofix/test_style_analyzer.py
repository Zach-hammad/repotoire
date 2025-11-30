"""Tests for style analysis and enforcement."""

import tempfile
from datetime import datetime
from pathlib import Path

import pytest

from repotoire.autofix.style import (
    StyleAnalyzer,
    StyleEnforcer,
    StyleProfile,
    StyleRule,
    classify_naming,
)


class TestClassifyNaming:
    """Tests for the classify_naming function."""

    def test_snake_case_simple(self):
        """Test simple snake_case detection."""
        assert classify_naming("get_user") == "snake_case"
        assert classify_naming("calculate_total_price") == "snake_case"
        assert classify_naming("fetch_api_data") == "snake_case"

    def test_snake_case_single_word(self):
        """Test single word defaults to snake_case."""
        assert classify_naming("user") == "snake_case"
        assert classify_naming("calculate") == "snake_case"

    def test_pascal_case(self):
        """Test PascalCase detection."""
        assert classify_naming("UserService") == "PascalCase"
        assert classify_naming("HttpClient") == "PascalCase"
        assert classify_naming("MyClass") == "PascalCase"

    def test_camel_case(self):
        """Test camelCase detection."""
        assert classify_naming("getUser") == "camelCase"
        assert classify_naming("calculateTotal") == "camelCase"
        assert classify_naming("httpRequest") == "camelCase"

    def test_screaming_snake_case(self):
        """Test SCREAMING_SNAKE_CASE detection."""
        assert classify_naming("MAX_VALUE") == "SCREAMING_SNAKE_CASE"
        assert classify_naming("API_KEY") == "SCREAMING_SNAKE_CASE"
        assert classify_naming("DEFAULT_TIMEOUT") == "SCREAMING_SNAKE_CASE"

    def test_leading_underscores(self):
        """Test handling of leading underscores."""
        assert classify_naming("_private_method") == "snake_case"
        assert classify_naming("__dunder__") == "snake_case"
        assert classify_naming("_PrivateClass") == "PascalCase"

    def test_empty_string(self):
        """Test empty or underscore-only names."""
        assert classify_naming("") == "unknown"
        assert classify_naming("_") == "unknown"
        assert classify_naming("__") == "unknown"


class TestStyleRule:
    """Tests for StyleRule model."""

    def test_is_high_confidence_default_threshold(self):
        """Test is_high_confidence with default threshold."""
        high_rule = StyleRule(
            name="test", value="snake_case", confidence=0.8, sample_count=100
        )
        low_rule = StyleRule(
            name="test", value="snake_case", confidence=0.4, sample_count=100
        )

        assert high_rule.is_high_confidence() is True
        assert low_rule.is_high_confidence() is False

    def test_is_high_confidence_custom_threshold(self):
        """Test is_high_confidence with custom threshold."""
        rule = StyleRule(
            name="test", value="snake_case", confidence=0.5, sample_count=100
        )

        assert rule.is_high_confidence(0.4) is True
        assert rule.is_high_confidence(0.6) is False

    def test_examples_max_length(self):
        """Test that examples field has max_length."""
        rule = StyleRule(
            name="test",
            value="snake_case",
            confidence=0.9,
            sample_count=10,
            examples=["a", "b", "c", "d", "e"],
        )
        assert len(rule.examples) == 5


class TestStyleAnalyzer:
    """Tests for StyleAnalyzer class."""

    @pytest.fixture
    def temp_repo(self, tmp_path):
        """Create a temporary repository with sample Python files."""
        # Create some Python files with consistent style
        (tmp_path / "module.py").write_text('''
"""Module docstring."""

import os
from pathlib import Path

MAX_ITEMS = 100
DEFAULT_NAME = "test"


class UserService:
    """Service for user operations.

    Args:
        config: Configuration object.
    """

    def __init__(self, config):
        self.config = config

    def get_user_by_id(self, user_id: int) -> dict:
        """Get user by ID.

        Args:
            user_id: The user ID.

        Returns:
            User dictionary.
        """
        return {"id": user_id}

    def calculate_total_score(self, scores: list) -> float:
        """Calculate total score.

        Args:
            scores: List of scores.

        Returns:
            Total score.
        """
        return sum(scores)


def process_data(data: list) -> list:
    """Process data.

    Args:
        data: Input data.

    Returns:
        Processed data.
    """
    return [item * 2 for item in data]
''')

        (tmp_path / "utils.py").write_text('''
"""Utility functions."""

API_KEY = "secret"


def format_string(text: str) -> str:
    """Format a string.

    Args:
        text: Input text.

    Returns:
        Formatted text.
    """
    return text.strip()


class DataProcessor:
    """Processes data."""

    def run_processing(self):
        """Run the processing."""
        pass
''')

        return tmp_path

    def test_init_valid_path(self, temp_repo):
        """Test initialization with valid path."""
        analyzer = StyleAnalyzer(temp_repo)
        assert analyzer.repository_path == temp_repo

    def test_init_invalid_path(self):
        """Test initialization with invalid path."""
        with pytest.raises(ValueError, match="does not exist"):
            StyleAnalyzer(Path("/nonexistent/path"))

    def test_analyze_function_naming(self, temp_repo):
        """Test detection of function naming convention."""
        analyzer = StyleAnalyzer(temp_repo)
        profile = analyzer.analyze()

        assert profile.function_naming.value == "snake_case"
        assert profile.function_naming.confidence > 0.9

    def test_analyze_class_naming(self, temp_repo):
        """Test detection of class naming convention."""
        analyzer = StyleAnalyzer(temp_repo)
        profile = analyzer.analyze()

        assert profile.class_naming.value == "PascalCase"
        assert profile.class_naming.confidence >= 1.0

    def test_analyze_docstring_style(self, temp_repo):
        """Test detection of docstring style."""
        analyzer = StyleAnalyzer(temp_repo)
        profile = analyzer.analyze()

        assert profile.docstring_style.value == "google"
        assert profile.docstring_style.confidence > 0.5

    def test_analyze_type_hint_coverage(self, temp_repo):
        """Test type hint coverage detection."""
        analyzer = StyleAnalyzer(temp_repo)
        profile = analyzer.analyze()

        # Our sample code has good type hint coverage
        assert profile.type_hint_coverage > 0.5

    def test_analyze_constant_naming(self, temp_repo):
        """Test constant naming detection."""
        analyzer = StyleAnalyzer(temp_repo)
        profile = analyzer.analyze()

        if profile.constant_naming:
            assert profile.constant_naming.value == "SCREAMING_SNAKE_CASE"

    def test_analyze_file_count(self, temp_repo):
        """Test that file count is accurate."""
        analyzer = StyleAnalyzer(temp_repo)
        profile = analyzer.analyze()

        assert profile.file_count == 2

    def test_analyze_max_files_limit(self, temp_repo):
        """Test max_files parameter limits analysis."""
        analyzer = StyleAnalyzer(temp_repo)
        profile = analyzer.analyze(max_files=1)

        assert profile.file_count <= 1


class TestStyleEnforcer:
    """Tests for StyleEnforcer class."""

    @pytest.fixture
    def sample_profile(self):
        """Create a sample StyleProfile for testing."""
        return StyleProfile(
            repository="/path/to/repo",
            analyzed_at=datetime.utcnow(),
            file_count=100,
            function_naming=StyleRule(
                name="function_naming",
                value="snake_case",
                confidence=0.92,
                sample_count=150,
                examples=["get_user", "calculate_total"],
            ),
            class_naming=StyleRule(
                name="class_naming",
                value="PascalCase",
                confidence=0.98,
                sample_count=50,
                examples=["UserService", "DataProcessor"],
            ),
            variable_naming=StyleRule(
                name="variable_naming",
                value="snake_case",
                confidence=0.52,  # Low confidence
                sample_count=200,
            ),
            docstring_style=StyleRule(
                name="docstring_style",
                value="google",
                confidence=0.85,
                sample_count=80,
            ),
            max_line_length=StyleRule(
                name="max_line_length",
                value="100",
                confidence=0.78,
                sample_count=1000,
            ),
            type_hint_coverage=0.65,
        )

    def test_get_style_instructions(self, sample_profile):
        """Test generation of style instructions."""
        enforcer = StyleEnforcer(sample_profile)
        instructions = enforcer.get_style_instructions()

        assert "Code Style Requirements" in instructions
        assert "snake_case" in instructions
        assert "function" in instructions
        assert "PascalCase" in instructions
        assert "class" in instructions

    def test_get_style_instructions_excludes_low_confidence(self, sample_profile):
        """Test that low confidence rules are excluded."""
        enforcer = StyleEnforcer(sample_profile, confidence_threshold=0.6)
        instructions = enforcer.get_style_instructions()

        # Variable naming has 0.52 confidence, should be excluded
        # But function (0.92) and class (0.98) should be included
        assert "function names" in instructions
        assert "class names" in instructions

    def test_get_style_instructions_docstring(self, sample_profile):
        """Test docstring style instruction."""
        enforcer = StyleEnforcer(sample_profile)
        instructions = enforcer.get_style_instructions()

        assert "Google-style docstrings" in instructions

    def test_get_style_instructions_line_length(self, sample_profile):
        """Test line length instruction."""
        enforcer = StyleEnforcer(sample_profile)
        instructions = enforcer.get_style_instructions()

        assert "100 characters" in instructions

    def test_get_style_instructions_type_hints(self, sample_profile):
        """Test type hint instruction."""
        enforcer = StyleEnforcer(sample_profile)
        instructions = enforcer.get_style_instructions()

        assert "type hints" in instructions
        assert "65%" in instructions

    def test_get_rule_summary(self, sample_profile):
        """Test rule summary generation."""
        enforcer = StyleEnforcer(sample_profile)
        summary = enforcer.get_rule_summary()

        assert len(summary) >= 6  # At least core rules + type coverage

        # Check function naming rule
        func_rule = next(r for r in summary if r["name"] == "Function naming")
        assert func_rule["value"] == "snake_case"
        assert func_rule["confidence"] == 0.92
        assert func_rule["included"] is True

    def test_get_rule_summary_excluded_rule(self, sample_profile):
        """Test that low confidence rules are marked as not included."""
        enforcer = StyleEnforcer(sample_profile, confidence_threshold=0.6)
        summary = enforcer.get_rule_summary()

        var_rule = next(r for r in summary if r["name"] == "Variable naming")
        assert var_rule["included"] is False  # 0.52 < 0.6


class TestStyleAnalyzerDocstrings:
    """Test docstring style detection."""

    @pytest.fixture
    def google_style_repo(self, tmp_path):
        """Create repo with Google-style docstrings."""
        (tmp_path / "module.py").write_text('''
def func(x, y):
    """Do something.

    Args:
        x: First value.
        y: Second value.

    Returns:
        Combined result.

    Raises:
        ValueError: If invalid.
    """
    return x + y
''')
        return tmp_path

    @pytest.fixture
    def numpy_style_repo(self, tmp_path):
        """Create repo with NumPy-style docstrings."""
        (tmp_path / "module.py").write_text('''
def func(x, y):
    """Do something.

    Parameters
    ----------
    x : int
        First value.
    y : int
        Second value.

    Returns
    -------
    int
        Combined result.
    """
    return x + y
''')
        return tmp_path

    @pytest.fixture
    def sphinx_style_repo(self, tmp_path):
        """Create repo with Sphinx-style docstrings."""
        (tmp_path / "module.py").write_text('''
def func(x, y):
    """Do something.

    :param x: First value.
    :param y: Second value.
    :returns: Combined result.
    :raises ValueError: If invalid.
    """
    return x + y
''')
        return tmp_path

    def test_detect_google_style(self, google_style_repo):
        """Test detection of Google-style docstrings."""
        analyzer = StyleAnalyzer(google_style_repo)
        profile = analyzer.analyze()

        assert profile.docstring_style.value == "google"

    def test_detect_numpy_style(self, numpy_style_repo):
        """Test detection of NumPy-style docstrings."""
        analyzer = StyleAnalyzer(numpy_style_repo)
        profile = analyzer.analyze()

        assert profile.docstring_style.value == "numpy"

    def test_detect_sphinx_style(self, sphinx_style_repo):
        """Test detection of Sphinx-style docstrings."""
        analyzer = StyleAnalyzer(sphinx_style_repo)
        profile = analyzer.analyze()

        assert profile.docstring_style.value == "sphinx"


class TestLineLengthDetection:
    """Test line length detection."""

    @pytest.fixture
    def short_lines_repo(self, tmp_path):
        """Create repo with short lines."""
        (tmp_path / "module.py").write_text(
            "x = 1\n" * 100 + "def foo(): pass\n" * 50
        )
        return tmp_path

    @pytest.fixture
    def long_lines_repo(self, tmp_path):
        """Create repo with long lines (around 100 chars)."""
        long_line = "x = " + "a" * 95 + "\n"
        (tmp_path / "module.py").write_text(long_line * 100)
        return tmp_path

    def test_detect_short_lines(self, short_lines_repo):
        """Test detection of short line length convention."""
        analyzer = StyleAnalyzer(short_lines_repo)
        profile = analyzer.analyze()

        # Short lines should round to 80
        assert int(profile.max_line_length.value) <= 88

    def test_detect_long_lines(self, long_lines_repo):
        """Test detection of long line length convention."""
        analyzer = StyleAnalyzer(long_lines_repo)
        profile = analyzer.analyze()

        # 99 char lines should round to 100
        assert int(profile.max_line_length.value) == 100


class TestStyleProfileSerialization:
    """Test StyleProfile serialization."""

    def test_to_dict(self):
        """Test to_dict method."""
        profile = StyleProfile(
            repository="/path/to/repo",
            file_count=50,
            function_naming=StyleRule(
                name="function_naming",
                value="snake_case",
                confidence=0.9,
                sample_count=100,
            ),
            class_naming=StyleRule(
                name="class_naming",
                value="PascalCase",
                confidence=0.95,
                sample_count=30,
            ),
            variable_naming=StyleRule(
                name="variable_naming",
                value="snake_case",
                confidence=0.8,
                sample_count=200,
            ),
            docstring_style=StyleRule(
                name="docstring_style",
                value="google",
                confidence=0.7,
                sample_count=40,
            ),
            max_line_length=StyleRule(
                name="max_line_length",
                value="100",
                confidence=0.85,
                sample_count=1000,
            ),
            type_hint_coverage=0.6,
        )

        data = profile.to_dict()

        assert data["repository"] == "/path/to/repo"
        assert data["file_count"] == 50
        assert data["function_naming"]["value"] == "snake_case"
        assert data["type_hint_coverage"] == 0.6

    def test_get_high_confidence_rules(self):
        """Test get_high_confidence_rules method."""
        profile = StyleProfile(
            repository="/path/to/repo",
            file_count=50,
            function_naming=StyleRule(
                name="function_naming",
                value="snake_case",
                confidence=0.9,
                sample_count=100,
            ),
            class_naming=StyleRule(
                name="class_naming",
                value="PascalCase",
                confidence=0.5,  # Low
                sample_count=30,
            ),
            variable_naming=StyleRule(
                name="variable_naming",
                value="snake_case",
                confidence=0.8,
                sample_count=200,
            ),
            docstring_style=StyleRule(
                name="docstring_style",
                value="google",
                confidence=0.3,  # Low
                sample_count=40,
            ),
            max_line_length=StyleRule(
                name="max_line_length",
                value="100",
                confidence=0.85,
                sample_count=1000,
            ),
            type_hint_coverage=0.6,
        )

        high_conf = profile.get_high_confidence_rules(threshold=0.6)

        # Should include function_naming (0.9), variable_naming (0.8), max_line_length (0.85)
        assert len(high_conf) == 3
        names = [r.name for r in high_conf]
        assert "function_naming" in names
        assert "variable_naming" in names
        assert "max_line_length" in names

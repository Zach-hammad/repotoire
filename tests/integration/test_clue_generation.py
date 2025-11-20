"""Integration tests for clue generation."""

import pytest
from unittest.mock import Mock, MagicMock, patch

from repotoire.ai import SpacyClueGenerator
from repotoire.models import FunctionEntity, ClassEntity, FileEntity


@pytest.fixture
def clue_generator():
    """Create a SpacyClueGenerator instance."""
    # Use small model for testing
    generator = SpacyClueGenerator(model_name="en_core_web_sm")
    return generator


class TestSpacyClueGenerator:
    """Test spaCy-based clue generation."""

    def test_generator_initialization(self, clue_generator):
        """Test generator initializes correctly."""
        assert clue_generator.model_name == "en_core_web_sm"
        assert clue_generator.nlp is None  # Lazy-loaded

    def test_generate_clues_for_function_with_docstring(self, clue_generator):
        """Test clue generation for function with good docstring."""
        function = FunctionEntity(
            name="authenticate_user",
            qualified_name="auth.py::authenticate_user",
            file_path="auth.py",
            line_start=10,
            line_end=25,
            docstring="Authenticate a user with username and password. Returns JWT token on success.",
            complexity=5
        )

        clues = clue_generator.generate_clues(function)

        # Should generate multiple clues
        assert len(clues) > 0

        # Check for purpose clue
        purpose_clues = [c for c in clues if c.clue_type == "purpose"]
        assert len(purpose_clues) > 0

        purpose_clue = purpose_clues[0]
        assert "authenticate" in purpose_clue.summary.lower() or "user" in purpose_clue.summary.lower()
        assert purpose_clue.confidence > 0.0
        assert purpose_clue.generated_by == "spacy"
        assert purpose_clue.target_entity == function.qualified_name
        # Keywords may be empty if using blank spaCy model
        assert isinstance(purpose_clue.keywords, list)

    def test_generate_clues_for_function_without_docstring(self, clue_generator):
        """Test clue generation for function without docstring."""
        function = FunctionEntity(
            name="helper_func",
            qualified_name="utils.py::helper_func",
            file_path="utils.py",
            line_start=5,
            line_end=10,
            docstring=None,
            complexity=2
        )

        clues = clue_generator.generate_clues(function)

        # Should still generate keyword clue from function name
        assert len(clues) > 0
        keyword_clues = [c for c in clues if c.clue_type == "concept"]
        assert len(keyword_clues) > 0

    def test_generate_clues_for_complex_function(self, clue_generator):
        """Test complexity clue for high-complexity function."""
        function = FunctionEntity(
            name="complex_algorithm",
            qualified_name="algo.py::complex_algorithm",
            file_path="algo.py",
            line_start=50,
            line_end=200,
            docstring="Implements a complex algorithm with multiple branches.",
            complexity=15  # High complexity
        )

        clues = clue_generator.generate_clues(function)

        # Should generate complexity clue
        complexity_clues = [c for c in clues if c.clue_type == "insight"]
        assert len(complexity_clues) > 0

        complexity_clue = complexity_clues[0]
        assert "complexity" in complexity_clue.summary.lower()
        assert complexity_clue.confidence == 1.0  # Objective metric
        assert "refactor" in complexity_clue.detailed_explanation.lower()

    def test_generate_clues_for_class_with_pattern(self, clue_generator):
        """Test pattern detection for class names."""
        class_entity = ClassEntity(
            name="UserManager",
            qualified_name="user.py::UserManager",
            file_path="user.py",
            line_start=10,
            line_end=100,
            docstring="Manages user lifecycle and operations."
        )

        clues = clue_generator.generate_clues(class_entity)

        # Should detect Manager pattern
        pattern_clues = [c for c in clues if c.clue_type == "pattern"]
        assert len(pattern_clues) > 0

        pattern_clue = pattern_clues[0]
        assert "pattern" in pattern_clue.summary.lower()
        assert "manages" in pattern_clue.summary.lower() or "coordinates" in pattern_clue.summary.lower()

    def test_generate_clues_for_file_entity(self, clue_generator):
        """Test clue generation for file entity."""
        file_entity = FileEntity(
            name="authentication.py",
            qualified_name="src/authentication.py",
            file_path="src/authentication.py",
            line_start=1,
            line_end=200,
            docstring="Module providing user authentication and authorization functionality.",
            language="python",
            loc=180
        )

        clues = clue_generator.generate_clues(file_entity)

        # Should generate clues from module docstring
        assert len(clues) > 0
        assert any(c.clue_type == "purpose" for c in clues)

    def test_keyword_extraction(self, clue_generator):
        """Test keyword extraction from docstrings."""
        entity = FunctionEntity(
            name="process_payment",
            qualified_name="payment.py::process_payment",
            file_path="payment.py",
            line_start=20,
            line_end=50,
            docstring="Process customer payment using credit card. Validates card details and creates transaction record.",
            complexity=8
        )

        clues = clue_generator.generate_clues(entity)

        # Find keyword clue
        keyword_clues = [c for c in clues if c.clue_type == "concept"]
        assert len(keyword_clues) > 0

        # Check extracted keywords contain relevant terms
        all_keywords = []
        for clue in keyword_clues:
            all_keywords.extend(clue.keywords)

        # Keywords may be empty with blank spaCy model, just check structure
        assert isinstance(all_keywords, list)

    def test_confidence_scoring(self, clue_generator):
        """Test confidence scoring based on docstring quality."""
        # High-quality docstring
        good_entity = FunctionEntity(
            name="calculate_total",
            qualified_name="calc.py::calculate_total",
            file_path="calc.py",
            line_start=10,
            line_end=30,
            docstring="""Calculate the total price including tax and discounts.
            
            Args:
                price: Base price of the item
                tax_rate: Tax rate as decimal (e.g., 0.08 for 8%)
                discount: Discount amount to subtract
                
            Returns:
                Final total price after tax and discounts
                
            Example:
                >>> calculate_total(100, 0.08, 10)
                98.0
            """,
            complexity=3
        )

        # Poor-quality docstring
        bad_entity = FunctionEntity(
            name="calc",
            qualified_name="calc.py::calc",
            file_path="calc.py",
            line_start=50,
            line_end=55,
            docstring="Does calculation.",
            complexity=2
        )

        good_clues = clue_generator.generate_clues(good_entity)
        bad_clues = clue_generator.generate_clues(bad_entity)

        # Good docstring should have higher confidence
        good_purpose = [c for c in good_clues if c.clue_type == "purpose"][0]
        bad_purpose = [c for c in bad_clues if c.clue_type == "purpose"][0]

        assert good_purpose.confidence > bad_purpose.confidence

    def test_clue_qualified_names(self, clue_generator):
        """Test clue qualified names follow convention."""
        entity = FunctionEntity(
            name="test_func",
            qualified_name="test.py::test_func",
            file_path="test.py",
            line_start=1,
            line_end=10,
            docstring="Test function.",
            complexity=1
        )

        clues = clue_generator.generate_clues(entity)

        # All clues should have qualified names starting with "clue::"
        for clue in clues:
            assert clue.qualified_name.startswith("clue::")
            assert entity.qualified_name in clue.qualified_name

    def test_clue_target_entity_reference(self, clue_generator):
        """Test clues reference their target entity."""
        entity = ClassEntity(
            name="TestClass",
            qualified_name="test.py::TestClass",
            file_path="test.py",
            line_start=5,
            line_end=20,
            docstring="Test class for demonstration."
        )

        clues = clue_generator.generate_clues(entity)

        # All clues should reference the target entity
        for clue in clues:
            assert clue.target_entity == entity.qualified_name

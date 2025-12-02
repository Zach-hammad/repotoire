"""Pytest configuration and fixtures for calculator tests."""

import sys
from pathlib import Path

import pytest

# Add src directory to path
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))


@pytest.fixture
def calculator():
    """Create a Calculator instance for testing."""
    from calculator import Calculator

    return Calculator()


@pytest.fixture
def zero_pair():
    """Provide a pair of numbers that sum to zero."""
    return (5, -5)

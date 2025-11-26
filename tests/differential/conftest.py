"""
Shared fixtures and Hypothesis configuration for differential tests.

Configuration:
- Default 1000 examples per test (10000 for thorough mode)
- Reproducible seeds for CI
- Custom strategies for domain types
"""

import pytest
from hypothesis import settings, Verbosity, Phase

# Register Hypothesis profiles
settings.register_profile(
    "ci",
    max_examples=1000,
    deadline=None,  # Disable deadline for CI
    print_blob=True,  # Print reproduction info on failure
    phases=[Phase.explicit, Phase.reuse, Phase.generate, Phase.shrink],
)

settings.register_profile(
    "thorough",
    max_examples=10000,
    deadline=None,
    print_blob=True,
)

settings.register_profile(
    "dev",
    max_examples=100,
    deadline=None,
    print_blob=True,
)

settings.register_profile(
    "debug",
    max_examples=10,
    verbosity=Verbosity.verbose,
    deadline=None,
)

# Load CI profile by default
settings.load_profile("ci")


@pytest.fixture
def lean_severity_order():
    """Severity ordering as defined in Lean."""
    from repotoire.models import Severity
    return [Severity.INFO, Severity.LOW, Severity.MEDIUM, Severity.HIGH, Severity.CRITICAL]


@pytest.fixture
def lean_severity_weights():
    """Severity weight mapping as defined in Lean (as percentages 0-100)."""
    from repotoire.models import Severity
    return {
        Severity.INFO: 20,
        Severity.LOW: 40,
        Severity.MEDIUM: 60,
        Severity.HIGH: 80,
        Severity.CRITICAL: 100,
    }


@pytest.fixture
def lean_priority_weights():
    """Priority score weights as defined in Lean (as percentages)."""
    return {
        "severity": 40,
        "confidence": 30,
        "agreement": 30,
    }


@pytest.fixture
def lean_health_weights():
    """Health score weights as defined in Lean (as percentages)."""
    return {
        "structure": 40,
        "quality": 30,
        "architecture": 30,
    }


@pytest.fixture
def lean_grade_thresholds():
    """Grade thresholds as defined in Lean."""
    return {
        "A": 90,
        "B": 80,
        "C": 70,
        "D": 60,
        "F": 0,
    }

"""
Differential Testing Framework for Lean/Python Verification.

This package contains property-based tests using Hypothesis that validate
the Python implementation matches the formally verified Lean specifications.

The tests generate thousands of random inputs and verify that Python produces
results consistent with the properties proven in Lean 4.

Lean specifications: lean/Repotoire/
Python implementations: repotoire/

Usage:
    pytest tests/differential/ -v
    pytest tests/differential/ -v --hypothesis-seed=42  # Reproducible
    pytest tests/differential/ -n auto  # Parallel execution
"""

"""
Module B that imports Module A - part of circular dependency test fixture.
"""

from module_a import function_in_a


def helper_from_b():
    """Helper function in module B."""
    return "B helper"


def function_in_b():
    """Function in module B that uses module A."""
    return function_in_a() + " called from B"

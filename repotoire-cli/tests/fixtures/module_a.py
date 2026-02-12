"""
Module A that imports Module B - part of circular dependency test fixture.
"""

from module_b import helper_from_b


def function_in_a():
    """Function in module A that uses module B."""
    return helper_from_b() + " called from A"


def another_function_in_a():
    """Another function in A."""
    return "A standalone"

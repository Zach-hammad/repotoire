"""Test fixture: Various nested function patterns.

This file tests nested function extraction at multiple depths.
"""


def single_level_nested():
    """Function with single level nesting."""
    def inner():
        return "inner"
    return inner()


def double_level_nested():
    """Function with two levels of nesting."""
    def middle():
        def deep():
            return "deep"
        return deep()
    return middle()


def triple_level_nested():
    """Function with three levels of nesting."""
    def level_one():
        def level_two():
            def level_three():
                return "level_three"
            return level_three()
        return level_two()
    return level_one()


def multiple_siblings():
    """Function with multiple nested functions at same level."""
    def sibling_one():
        return 1

    def sibling_two():
        return 2

    def sibling_three():
        return 3

    return sibling_one() + sibling_two() + sibling_three()


def mixed_depth_nested():
    """Function with nested functions at different depths."""
    def shallow():
        return "shallow"

    def has_deeper():
        def deeper():
            return "deeper"
        return deeper()

    return shallow() + has_deeper()


async def async_nested_outer():
    """Async function with nested functions."""
    async def async_inner():
        return "async_inner"

    def sync_inner():
        return "sync_inner"

    return await async_inner() + sync_inner()


def decorator_factory_pattern():
    """Nested functions forming a decorator factory pattern."""
    def actual_decorator(func):
        def wrapper(*args, **kwargs):
            return func(*args, **kwargs)
        return wrapper
    return actual_decorator


class ClassWithNestedFunctions:
    """Class containing methods with nested functions."""

    def method_with_nested(self):
        """Method with nested function."""
        def nested_in_method():
            return "nested_in_method"
        return nested_in_method()

    def method_with_deep_nested(self):
        """Method with deeply nested function."""
        def level_one():
            def level_two():
                return "level_two_in_method"
            return level_two()
        return level_one()

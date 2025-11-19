"""Sample Python file for testing."""

import os
from pathlib import Path


class ParentClass:
    """A parent class."""

    def parent_method(self):
        """Parent method."""
        return "parent"


class ChildClass(ParentClass):
    """A child class that inherits from ParentClass."""

    def parent_method(self):
        """Override parent method."""
        return "child"

    def child_method(self):
        """Child specific method."""
        self.parent_method()
        return "child method"


def used_function():
    """This function is called."""
    return 42


def unused_function():
    """This function is never called - should be detected as dead code."""
    return 999


def caller_function():
    """Function that calls other functions."""
    result = used_function()
    obj = ChildClass()
    obj.child_method()
    return result


# Entry point
if __name__ == "__main__":
    caller_function()

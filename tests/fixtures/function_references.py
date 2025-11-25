"""Test fixture: Function reference patterns (USES relationships).

This file tests USES relationship extraction for functions passed as arguments
and functions returned from other functions.
"""


def helper_function():
    """A helper function that will be referenced."""
    return "helper"


def another_helper():
    """Another helper function."""
    return "another"


def pass_function_as_argument():
    """Pass a function as an argument - creates USES relationship."""
    result = some_processor(helper_function)
    return result


def some_processor(func):
    """Process a function - receives function reference."""
    return func()


def pass_multiple_functions():
    """Pass multiple functions as arguments."""
    return executor(helper_function, another_helper)


def executor(func1, func2):
    """Execute multiple functions."""
    return func1() + func2()


def return_function():
    """Return a function reference - creates USES relationship."""
    return helper_function


def return_nested_function():
    """Return a nested function - creates USES relationship."""
    def nested():
        return "nested"
    return nested


def conditional_return_function(condition):
    """Conditionally return a function."""
    if condition:
        return helper_function
    else:
        return another_helper


def higher_order_function():
    """Higher-order function returning a function."""
    def created_function():
        return "created"
    return created_function


class FunctionReferenceInClass:
    """Class with methods that use function references."""

    def method_uses_function(self):
        """Method that uses external function."""
        return mapper(helper_function)

    def method_returns_function(self):
        """Method that returns a function."""
        return helper_function

    def method_passes_method(self):
        """Method that passes another method as argument."""
        return processor(self._helper_method)

    def _helper_method(self):
        """Private helper method."""
        return "private"


def mapper(func):
    """Map function."""
    return func()


def processor(func):
    """Process function."""
    return func()


# Callback pattern
def register_callback(callback):
    """Register a callback function."""
    callbacks.append(callback)


callbacks = []


def on_event():
    """Event handler to be registered."""
    pass


def setup_callbacks():
    """Set up callbacks - uses function references."""
    register_callback(on_event)
    register_callback(helper_function)


# Decorator factory pattern with function references
def make_decorator(handler):
    """Create a decorator using a handler function."""
    def decorator(func):
        def wrapper(*args, **kwargs):
            handler()
            return func(*args, **kwargs)
        return wrapper
    return decorator


def my_handler():
    """Handler for decorator."""
    pass


# Creates USES relationship: make_decorator -> my_handler
custom_decorator = make_decorator(my_handler)

"""Test fixture: Closure patterns.

These patterns test closure detection and USES relationships for captured variables.
"""


def simple_closure():
    """Simple closure pattern."""
    value = 10

    def inner():
        return value  # Captures 'value'

    return inner


def closure_with_modification():
    """Closure that modifies captured variable."""
    counter = [0]  # Using list for mutability

    def increment():
        counter[0] += 1
        return counter[0]

    return increment


def closure_factory(initial_value):
    """Factory function that creates closures."""
    current = initial_value

    def get():
        return current

    def set_value(new_value):
        nonlocal current
        current = new_value

    return get, set_value


def closure_chain():
    """Chain of closures."""
    def outer(x):
        def middle(y):
            def inner(z):
                return x + y + z  # Captures x, y from outer scopes
            return inner
        return middle
    return outer


def closure_with_function_reference():
    """Closure that references other functions."""
    def helper():
        return "helped"

    def main():
        return helper()  # CALLS helper

    return main


def closure_returning_function():
    """Closure that returns a function reference."""
    def target():
        return "target"

    def get_target():
        return target  # USES target

    return get_target


class ClosureInClass:
    """Class with methods that create closures."""

    def __init__(self, value):
        self.value = value

    def create_closure(self):
        """Create a closure that captures instance variable."""
        captured = self.value

        def closure_func():
            return captured

        return closure_func

    def create_callback(self):
        """Create a callback closure."""
        def callback(data):
            return self.process(data)  # References method

        return callback

    def process(self, data):
        """Process data."""
        return data * 2


# Partial application pattern
def partial_applier(func, *args):
    """Partial application using closure."""
    def applied(*more_args):
        return func(*args, *more_args)
    return applied


def add(a, b, c):
    """Simple addition function."""
    return a + b + c


# Creates closure
add_five = partial_applier(add, 5)


# Event handler pattern with closures
def create_handler(event_name):
    """Create an event handler with captured event name."""
    def handler(event_data):
        print(f"Handling {event_name}: {event_data}")
        return process_event(event_name, event_data)

    return handler


def process_event(name, data):
    """Process an event."""
    return f"{name}: {data}"


# Multiple closures sharing state
def create_counter_suite():
    """Create multiple closures sharing the same state."""
    count = 0

    def increment():
        nonlocal count
        count += 1
        return count

    def decrement():
        nonlocal count
        count -= 1
        return count

    def get():
        return count

    def reset():
        nonlocal count
        count = 0

    return increment, decrement, get, reset

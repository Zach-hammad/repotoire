"""Simple Python project for testing.

This is a basic Python module demonstrating clean code practices.
"""

from typing import List, Optional


def greet(name: str) -> str:
    """Greet someone by name.

    Args:
        name: The name of the person to greet.

    Returns:
        A greeting message.
    """
    return f"Hello, {name}!"


def add(a: int, b: int) -> int:
    """Add two numbers.

    Args:
        a: First number.
        b: Second number.

    Returns:
        Sum of a and b.
    """
    return a + b


def multiply(a: int, b: int) -> int:
    """Multiply two numbers.

    Args:
        a: First factor.
        b: Second factor.

    Returns:
        Product of a and b.
    """
    return a * b


def find_max(numbers: List[int]) -> Optional[int]:
    """Find the maximum value in a list.

    Args:
        numbers: List of integers to search.

    Returns:
        Maximum value, or None if list is empty.
    """
    if not numbers:
        return None
    return max(numbers)


class Calculator:
    """A simple calculator class."""

    def __init__(self, initial_value: int = 0):
        """Initialize calculator with an initial value.

        Args:
            initial_value: Starting value for calculations.
        """
        self.value = initial_value

    def add(self, n: int) -> "Calculator":
        """Add to current value.

        Args:
            n: Number to add.

        Returns:
            Self for method chaining.
        """
        self.value += n
        return self

    def subtract(self, n: int) -> "Calculator":
        """Subtract from current value.

        Args:
            n: Number to subtract.

        Returns:
            Self for method chaining.
        """
        self.value -= n
        return self

    def result(self) -> int:
        """Get the current result.

        Returns:
            Current calculated value.
        """
        return self.value


if __name__ == "__main__":
    print(greet("World"))
    print(f"2 + 3 = {add(2, 3)}")
    print(f"4 * 5 = {multiply(4, 5)}")
    print(f"Max of [1, 5, 3] = {find_max([1, 5, 3])}")

    calc = Calculator(10).add(5).subtract(3)
    print(f"Calculator result: {calc.result()}")

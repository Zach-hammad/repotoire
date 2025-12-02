"""Calculator module with full test coverage."""

from typing import Union

Number = Union[int, float]


class CalculatorError(Exception):
    """Base exception for calculator errors."""

    pass


class DivisionByZeroError(CalculatorError):
    """Raised when attempting to divide by zero."""

    pass


class Calculator:
    """A calculator class with basic arithmetic operations.

    This class provides methods for addition, subtraction,
    multiplication, and division with proper error handling.

    Example:
        >>> calc = Calculator()
        >>> calc.add(5, 3)
        8
        >>> calc.divide(10, 2)
        5.0
    """

    def add(self, a: Number, b: Number) -> Number:
        """Add two numbers.

        Args:
            a: First number.
            b: Second number.

        Returns:
            Sum of a and b.
        """
        return a + b

    def subtract(self, a: Number, b: Number) -> Number:
        """Subtract b from a.

        Args:
            a: Number to subtract from.
            b: Number to subtract.

        Returns:
            Difference of a and b.
        """
        return a - b

    def multiply(self, a: Number, b: Number) -> Number:
        """Multiply two numbers.

        Args:
            a: First factor.
            b: Second factor.

        Returns:
            Product of a and b.
        """
        return a * b

    def divide(self, a: Number, b: Number) -> float:
        """Divide a by b.

        Args:
            a: Dividend.
            b: Divisor.

        Returns:
            Quotient of a divided by b.

        Raises:
            DivisionByZeroError: If b is zero.
        """
        if b == 0:
            raise DivisionByZeroError("Cannot divide by zero")
        return a / b

    def power(self, base: Number, exponent: int) -> Number:
        """Raise base to the power of exponent.

        Args:
            base: The base number.
            exponent: The exponent (must be non-negative integer).

        Returns:
            base raised to the power of exponent.

        Raises:
            ValueError: If exponent is negative.
        """
        if exponent < 0:
            raise ValueError("Exponent must be non-negative")
        return base ** exponent

    def modulo(self, a: int, b: int) -> int:
        """Calculate a modulo b.

        Args:
            a: Dividend.
            b: Divisor.

        Returns:
            Remainder of a divided by b.

        Raises:
            DivisionByZeroError: If b is zero.
        """
        if b == 0:
            raise DivisionByZeroError("Cannot calculate modulo with divisor zero")
        return a % b

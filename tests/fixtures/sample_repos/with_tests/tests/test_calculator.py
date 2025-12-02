"""Comprehensive tests for the Calculator class."""

import pytest
from calculator import Calculator, DivisionByZeroError


class TestCalculatorAdd:
    """Tests for Calculator.add method."""

    def test_add_positive_numbers(self, calculator):
        """Test adding two positive numbers."""
        assert calculator.add(2, 3) == 5

    def test_add_negative_numbers(self, calculator):
        """Test adding two negative numbers."""
        assert calculator.add(-2, -3) == -5

    def test_add_mixed_numbers(self, calculator):
        """Test adding positive and negative numbers."""
        assert calculator.add(5, -3) == 2
        assert calculator.add(-5, 3) == -2

    def test_add_zero(self, calculator):
        """Test adding zero."""
        assert calculator.add(5, 0) == 5
        assert calculator.add(0, 5) == 5
        assert calculator.add(0, 0) == 0

    def test_add_floats(self, calculator):
        """Test adding floating point numbers."""
        assert calculator.add(2.5, 3.5) == 6.0
        assert calculator.add(1.1, 2.2) == pytest.approx(3.3)


class TestCalculatorSubtract:
    """Tests for Calculator.subtract method."""

    def test_subtract_positive_numbers(self, calculator):
        """Test subtracting positive numbers."""
        assert calculator.subtract(5, 3) == 2
        assert calculator.subtract(3, 5) == -2

    def test_subtract_negative_numbers(self, calculator):
        """Test subtracting negative numbers."""
        assert calculator.subtract(-5, -3) == -2

    def test_subtract_zero(self, calculator):
        """Test subtracting zero."""
        assert calculator.subtract(5, 0) == 5
        assert calculator.subtract(0, 5) == -5


class TestCalculatorMultiply:
    """Tests for Calculator.multiply method."""

    def test_multiply_positive_numbers(self, calculator):
        """Test multiplying positive numbers."""
        assert calculator.multiply(4, 3) == 12

    def test_multiply_negative_numbers(self, calculator):
        """Test multiplying negative numbers."""
        assert calculator.multiply(-4, -3) == 12
        assert calculator.multiply(-4, 3) == -12
        assert calculator.multiply(4, -3) == -12

    def test_multiply_by_zero(self, calculator):
        """Test multiplying by zero."""
        assert calculator.multiply(5, 0) == 0
        assert calculator.multiply(0, 5) == 0

    def test_multiply_by_one(self, calculator):
        """Test multiplying by one."""
        assert calculator.multiply(5, 1) == 5
        assert calculator.multiply(1, 5) == 5


class TestCalculatorDivide:
    """Tests for Calculator.divide method."""

    def test_divide_positive_numbers(self, calculator):
        """Test dividing positive numbers."""
        assert calculator.divide(10, 2) == 5.0
        assert calculator.divide(7, 2) == 3.5

    def test_divide_negative_numbers(self, calculator):
        """Test dividing negative numbers."""
        assert calculator.divide(-10, 2) == -5.0
        assert calculator.divide(10, -2) == -5.0
        assert calculator.divide(-10, -2) == 5.0

    def test_divide_by_one(self, calculator):
        """Test dividing by one."""
        assert calculator.divide(5, 1) == 5.0

    def test_divide_by_zero_raises(self, calculator):
        """Test that dividing by zero raises an error."""
        with pytest.raises(DivisionByZeroError) as exc_info:
            calculator.divide(10, 0)
        assert "Cannot divide by zero" in str(exc_info.value)


class TestCalculatorPower:
    """Tests for Calculator.power method."""

    def test_power_positive_exponent(self, calculator):
        """Test raising to positive exponent."""
        assert calculator.power(2, 3) == 8
        assert calculator.power(5, 2) == 25

    def test_power_zero_exponent(self, calculator):
        """Test raising to zero exponent."""
        assert calculator.power(5, 0) == 1
        assert calculator.power(0, 0) == 1  # 0^0 is conventionally 1

    def test_power_negative_base(self, calculator):
        """Test negative base."""
        assert calculator.power(-2, 2) == 4
        assert calculator.power(-2, 3) == -8

    def test_power_negative_exponent_raises(self, calculator):
        """Test that negative exponent raises an error."""
        with pytest.raises(ValueError) as exc_info:
            calculator.power(2, -1)
        assert "non-negative" in str(exc_info.value)


class TestCalculatorModulo:
    """Tests for Calculator.modulo method."""

    def test_modulo_positive_numbers(self, calculator):
        """Test modulo with positive numbers."""
        assert calculator.modulo(10, 3) == 1
        assert calculator.modulo(9, 3) == 0

    def test_modulo_by_zero_raises(self, calculator):
        """Test that modulo by zero raises an error."""
        with pytest.raises(DivisionByZeroError):
            calculator.modulo(10, 0)

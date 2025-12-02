"""Formatting utilities."""

from datetime import datetime
from decimal import Decimal
from typing import Optional


def format_currency(
    amount: Decimal,
    currency: str = "USD",
    locale: str = "en_US",
) -> str:
    """Format a decimal amount as currency.

    Args:
        amount: Amount to format.
        currency: Currency code (default: USD).
        locale: Locale for formatting (default: en_US).

    Returns:
        Formatted currency string.
    """
    symbols = {
        "USD": "$",
        "EUR": "\u20ac",  # Euro symbol
        "GBP": "\u00a3",  # Pound symbol
        "JPY": "\u00a5",  # Yen symbol
    }

    symbol = symbols.get(currency, currency)

    if locale == "en_US":
        # US format: $1,234.56
        formatted = f"{amount:,.2f}"
        return f"{symbol}{formatted}"
    else:
        # Generic format
        return f"{symbol}{amount:.2f}"


def format_date(
    date: datetime,
    format_string: Optional[str] = None,
    include_time: bool = False,
) -> str:
    """Format a datetime object as a string.

    Args:
        date: Datetime to format.
        format_string: Custom format string (optional).
        include_time: Include time in output (default: False).

    Returns:
        Formatted date string.
    """
    if format_string:
        return date.strftime(format_string)

    if include_time:
        return date.strftime("%Y-%m-%d %H:%M:%S")
    else:
        return date.strftime("%Y-%m-%d")


def format_phone(phone: str) -> str:
    """Format a phone number.

    Args:
        phone: Phone number to format.

    Returns:
        Formatted phone number.
    """
    # Remove non-numeric characters
    digits = ''.join(filter(str.isdigit, phone))

    if len(digits) == 10:
        # US format: (XXX) XXX-XXXX
        return f"({digits[:3]}) {digits[3:6]}-{digits[6:]}"
    elif len(digits) == 11 and digits[0] == '1':
        # US with country code: +1 (XXX) XXX-XXXX
        return f"+1 ({digits[1:4]}) {digits[4:7]}-{digits[7:]}"
    else:
        return phone  # Return original if can't format

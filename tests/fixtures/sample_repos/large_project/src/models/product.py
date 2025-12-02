"""Product model definitions."""

from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from enum import Enum
from typing import List, Optional


class Category(Enum):
    """Product category enumeration."""
    ELECTRONICS = "electronics"
    CLOTHING = "clothing"
    BOOKS = "books"
    HOME = "home"
    SPORTS = "sports"
    OTHER = "other"


@dataclass
class Product:
    """Product model representing an item for sale.

    Attributes:
        id: Unique product identifier.
        name: Product name.
        description: Product description.
        price: Product price in decimal format.
        category: Product category.
        stock: Available stock quantity.
        created_at: Product creation timestamp.
        is_available: Whether the product is available for purchase.
        tags: List of product tags for search.
    """
    id: int
    name: str
    description: str
    price: Decimal
    category: Category
    stock: int = 0
    created_at: datetime = field(default_factory=datetime.utcnow)
    is_available: bool = True
    tags: List[str] = field(default_factory=list)

    def __post_init__(self):
        """Validate product data after initialization."""
        if self.price < 0:
            raise ValueError("Price cannot be negative")
        if self.stock < 0:
            raise ValueError("Stock cannot be negative")

    def is_in_stock(self) -> bool:
        """Check if product is in stock.

        Returns:
            True if stock > 0 and product is available.
        """
        return self.stock > 0 and self.is_available

    def reduce_stock(self, quantity: int) -> bool:
        """Reduce stock by specified quantity.

        Args:
            quantity: Amount to reduce stock by.

        Returns:
            True if stock was reduced, False if insufficient stock.
        """
        if quantity < 0:
            raise ValueError("Quantity cannot be negative")
        if self.stock >= quantity:
            self.stock -= quantity
            return True
        return False

    def add_stock(self, quantity: int) -> None:
        """Add stock.

        Args:
            quantity: Amount to add to stock.
        """
        if quantity < 0:
            raise ValueError("Quantity cannot be negative")
        self.stock += quantity

    def apply_discount(self, percentage: float) -> Decimal:
        """Calculate discounted price.

        Args:
            percentage: Discount percentage (0-100).

        Returns:
            Discounted price.
        """
        if not 0 <= percentage <= 100:
            raise ValueError("Discount percentage must be between 0 and 100")
        discount = self.price * Decimal(percentage / 100)
        return self.price - discount

    def add_tag(self, tag: str) -> None:
        """Add a tag to the product.

        Args:
            tag: Tag to add.
        """
        tag = tag.lower().strip()
        if tag and tag not in self.tags:
            self.tags.append(tag)

    def remove_tag(self, tag: str) -> bool:
        """Remove a tag from the product.

        Args:
            tag: Tag to remove.

        Returns:
            True if tag was removed, False if not found.
        """
        tag = tag.lower().strip()
        if tag in self.tags:
            self.tags.remove(tag)
            return True
        return False

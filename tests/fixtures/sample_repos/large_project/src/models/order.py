"""Order model definitions."""

from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from enum import Enum
from typing import List, Optional

from .product import Product
from .user import User


class OrderStatus(Enum):
    """Order status enumeration."""
    PENDING = "pending"
    CONFIRMED = "confirmed"
    PROCESSING = "processing"
    SHIPPED = "shipped"
    DELIVERED = "delivered"
    CANCELLED = "cancelled"
    REFUNDED = "refunded"


@dataclass
class OrderItem:
    """Individual item in an order.

    Attributes:
        product_id: ID of the product.
        product_name: Name of the product at time of order.
        quantity: Quantity ordered.
        unit_price: Price per unit at time of order.
    """
    product_id: int
    product_name: str
    quantity: int
    unit_price: Decimal

    @property
    def total_price(self) -> Decimal:
        """Calculate total price for this item.

        Returns:
            Quantity multiplied by unit price.
        """
        return self.unit_price * self.quantity


@dataclass
class Order:
    """Order model representing a customer order.

    Attributes:
        id: Unique order identifier.
        user_id: ID of the user who placed the order.
        items: List of items in the order.
        status: Current order status.
        created_at: Order creation timestamp.
        updated_at: Last update timestamp.
        shipping_address: Shipping address.
        notes: Optional order notes.
    """
    id: int
    user_id: int
    items: List[OrderItem] = field(default_factory=list)
    status: OrderStatus = OrderStatus.PENDING
    created_at: datetime = field(default_factory=datetime.utcnow)
    updated_at: datetime = field(default_factory=datetime.utcnow)
    shipping_address: str = ""
    notes: Optional[str] = None

    @property
    def total(self) -> Decimal:
        """Calculate total order amount.

        Returns:
            Sum of all item totals.
        """
        return sum(item.total_price for item in self.items)

    @property
    def item_count(self) -> int:
        """Get total number of items.

        Returns:
            Total quantity of all items.
        """
        return sum(item.quantity for item in self.items)

    def add_item(self, product: Product, quantity: int) -> bool:
        """Add an item to the order.

        Args:
            product: Product to add.
            quantity: Quantity to add.

        Returns:
            True if item was added, False if product not available.
        """
        if not product.is_in_stock() or product.stock < quantity:
            return False

        # Check if product already in order
        for item in self.items:
            if item.product_id == product.id:
                item.quantity += quantity
                self._update_timestamp()
                return True

        # Add new item
        self.items.append(OrderItem(
            product_id=product.id,
            product_name=product.name,
            quantity=quantity,
            unit_price=product.price,
        ))
        self._update_timestamp()
        return True

    def remove_item(self, product_id: int) -> bool:
        """Remove an item from the order.

        Args:
            product_id: ID of the product to remove.

        Returns:
            True if item was removed, False if not found.
        """
        for i, item in enumerate(self.items):
            if item.product_id == product_id:
                del self.items[i]
                self._update_timestamp()
                return True
        return False

    def update_status(self, new_status: OrderStatus) -> None:
        """Update order status.

        Args:
            new_status: New status to set.
        """
        self.status = new_status
        self._update_timestamp()

    def cancel(self) -> bool:
        """Cancel the order.

        Returns:
            True if order was cancelled, False if not cancellable.
        """
        if self.status in (OrderStatus.SHIPPED, OrderStatus.DELIVERED):
            return False
        self.status = OrderStatus.CANCELLED
        self._update_timestamp()
        return True

    def can_be_modified(self) -> bool:
        """Check if order can be modified.

        Returns:
            True if order is in a modifiable state.
        """
        return self.status in (OrderStatus.PENDING, OrderStatus.CONFIRMED)

    def _update_timestamp(self) -> None:
        """Update the updated_at timestamp."""
        self.updated_at = datetime.utcnow()

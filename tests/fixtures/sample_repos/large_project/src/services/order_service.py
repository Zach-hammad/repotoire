"""Order service for managing order operations."""

from typing import Dict, List, Optional

from ..models.order import Order, OrderStatus
from ..models.product import Product
from .product_service import ProductService


class OrderService:
    """Service for managing order operations."""

    def __init__(self, product_service: ProductService):
        """Initialize order service.

        Args:
            product_service: Product service for product operations.
        """
        self._orders: Dict[int, Order] = {}
        self._next_id = 1
        self._product_service = product_service

    def create_order(
        self,
        user_id: int,
        shipping_address: str,
        notes: Optional[str] = None,
    ) -> Order:
        """Create a new order.

        Args:
            user_id: ID of the user placing the order.
            shipping_address: Shipping address.
            notes: Optional order notes.

        Returns:
            Created Order object.
        """
        order = Order(
            id=self._next_id,
            user_id=user_id,
            shipping_address=shipping_address,
            notes=notes,
        )

        self._orders[order.id] = order
        self._next_id += 1

        return order

    def get_order(self, order_id: int) -> Optional[Order]:
        """Get an order by ID.

        Args:
            order_id: Order ID to look up.

        Returns:
            Order object if found, None otherwise.
        """
        return self._orders.get(order_id)

    def get_user_orders(self, user_id: int) -> List[Order]:
        """Get all orders for a user.

        Args:
            user_id: User ID.

        Returns:
            List of user's orders.
        """
        return [o for o in self._orders.values() if o.user_id == user_id]

    def add_item_to_order(
        self,
        order_id: int,
        product_id: int,
        quantity: int,
    ) -> bool:
        """Add an item to an order.

        Args:
            order_id: Order ID.
            product_id: Product ID to add.
            quantity: Quantity to add.

        Returns:
            True if item added, False otherwise.
        """
        order = self.get_order(order_id)
        if not order or not order.can_be_modified():
            return False

        product = self._product_service.get_product(product_id)
        if not product:
            return False

        return order.add_item(product, quantity)

    def remove_item_from_order(self, order_id: int, product_id: int) -> bool:
        """Remove an item from an order.

        Args:
            order_id: Order ID.
            product_id: Product ID to remove.

        Returns:
            True if removed, False otherwise.
        """
        order = self.get_order(order_id)
        if not order or not order.can_be_modified():
            return False

        return order.remove_item(product_id)

    def confirm_order(self, order_id: int) -> bool:
        """Confirm an order and deduct stock.

        Args:
            order_id: Order ID to confirm.

        Returns:
            True if confirmed, False otherwise.
        """
        order = self.get_order(order_id)
        if not order or order.status != OrderStatus.PENDING:
            return False

        # Check and deduct stock for all items
        for item in order.items:
            product = self._product_service.get_product(item.product_id)
            if not product or not product.reduce_stock(item.quantity):
                return False

        order.update_status(OrderStatus.CONFIRMED)
        return True

    def cancel_order(self, order_id: int) -> bool:
        """Cancel an order and restore stock.

        Args:
            order_id: Order ID to cancel.

        Returns:
            True if cancelled, False otherwise.
        """
        order = self.get_order(order_id)
        if not order:
            return False

        # Restore stock if order was confirmed
        if order.status in (OrderStatus.CONFIRMED, OrderStatus.PROCESSING):
            for item in order.items:
                product = self._product_service.get_product(item.product_id)
                if product:
                    product.add_stock(item.quantity)

        return order.cancel()

    def ship_order(self, order_id: int) -> bool:
        """Mark order as shipped.

        Args:
            order_id: Order ID.

        Returns:
            True if updated, False otherwise.
        """
        order = self.get_order(order_id)
        if not order or order.status != OrderStatus.PROCESSING:
            return False

        order.update_status(OrderStatus.SHIPPED)
        return True

    def deliver_order(self, order_id: int) -> bool:
        """Mark order as delivered.

        Args:
            order_id: Order ID.

        Returns:
            True if updated, False otherwise.
        """
        order = self.get_order(order_id)
        if not order or order.status != OrderStatus.SHIPPED:
            return False

        order.update_status(OrderStatus.DELIVERED)
        return True

    def list_orders_by_status(self, status: OrderStatus) -> List[Order]:
        """List orders by status.

        Args:
            status: Status to filter by.

        Returns:
            List of orders with the given status.
        """
        return [o for o in self._orders.values() if o.status == status]

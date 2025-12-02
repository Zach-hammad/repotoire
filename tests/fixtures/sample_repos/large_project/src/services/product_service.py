"""Product service for managing product operations."""

from decimal import Decimal
from typing import Dict, List, Optional

from ..models.product import Product, Category


class ProductService:
    """Service for managing product operations."""

    def __init__(self):
        """Initialize the product service."""
        self._products: Dict[int, Product] = {}
        self._next_id = 1

    def create_product(
        self,
        name: str,
        description: str,
        price: Decimal,
        category: Category,
        stock: int = 0,
    ) -> Product:
        """Create a new product.

        Args:
            name: Product name.
            description: Product description.
            price: Product price.
            category: Product category.
            stock: Initial stock (default: 0).

        Returns:
            Created Product object.
        """
        product = Product(
            id=self._next_id,
            name=name,
            description=description,
            price=price,
            category=category,
            stock=stock,
        )

        self._products[product.id] = product
        self._next_id += 1

        return product

    def get_product(self, product_id: int) -> Optional[Product]:
        """Get a product by ID.

        Args:
            product_id: Product ID to look up.

        Returns:
            Product object if found, None otherwise.
        """
        return self._products.get(product_id)

    def list_products(
        self,
        category: Optional[Category] = None,
        in_stock_only: bool = False,
        min_price: Optional[Decimal] = None,
        max_price: Optional[Decimal] = None,
    ) -> List[Product]:
        """List products with optional filters.

        Args:
            category: Filter by category.
            in_stock_only: Only return in-stock products.
            min_price: Minimum price filter.
            max_price: Maximum price filter.

        Returns:
            List of products matching criteria.
        """
        products = list(self._products.values())

        if category:
            products = [p for p in products if p.category == category]

        if in_stock_only:
            products = [p for p in products if p.is_in_stock()]

        if min_price is not None:
            products = [p for p in products if p.price >= min_price]

        if max_price is not None:
            products = [p for p in products if p.price <= max_price]

        return products

    def search_products(self, query: str) -> List[Product]:
        """Search products by name or tags.

        Args:
            query: Search query.

        Returns:
            List of matching products.
        """
        query = query.lower()
        results = []

        for product in self._products.values():
            if query in product.name.lower():
                results.append(product)
            elif any(query in tag for tag in product.tags):
                results.append(product)

        return results

    def update_stock(self, product_id: int, quantity: int) -> bool:
        """Update product stock.

        Args:
            product_id: Product ID.
            quantity: New stock quantity.

        Returns:
            True if successful, False if product not found.
        """
        product = self.get_product(product_id)
        if not product:
            return False

        if quantity < 0:
            raise ValueError("Stock cannot be negative")

        product.stock = quantity
        return True

    def update_price(self, product_id: int, new_price: Decimal) -> bool:
        """Update product price.

        Args:
            product_id: Product ID.
            new_price: New price.

        Returns:
            True if successful, False if product not found.
        """
        product = self.get_product(product_id)
        if not product:
            return False

        if new_price < 0:
            raise ValueError("Price cannot be negative")

        product.price = new_price
        return True

    def delete_product(self, product_id: int) -> bool:
        """Delete a product.

        Args:
            product_id: Product ID to delete.

        Returns:
            True if deleted, False if not found.
        """
        if product_id in self._products:
            del self._products[product_id]
            return True
        return False

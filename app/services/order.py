from decimal import Decimal

from app.models.order import Order
from app.repositories.order import OrderRepository
from app.schemas.order import (
    OrderCreate,
    OrderItemResponse,
    OrderListResponse,
    OrderResponse,
    OrderUpdate,
)

MAX_ORDER_TOTAL = Decimal("9999999999.99")


class OrderNotFoundError(Exception):
    """Raised when an order identifier does not exist."""


class OrderCustomerNotFoundError(Exception):
    """Raised when an order references a missing customer."""


class OrderProductNotFoundError(Exception):
    """Raised when an order references a missing product."""


class InactiveOrderProductError(Exception):
    """Raised when an inactive product is included in an order."""


class OrderTotalOutOfRangeError(Exception):
    """Raised when a calculated total cannot fit Numeric(12, 2)."""


class OrderService:
    def __init__(self, repository: OrderRepository) -> None:
        self._repository = repository

    @staticmethod
    def _to_response(order: Order) -> OrderResponse:
        return OrderResponse(
            id=order.id,
            customer_id=order.customer_id,
            status=order.status,
            total_amount=order.total_amount,
            items=[
                OrderItemResponse.model_validate(item)
                for item in sorted(order.items, key=lambda item: item.id)
            ],
            created_at=order.created_at,
            updated_at=order.updated_at,
        )

    def create(self, data: OrderCreate) -> OrderResponse:
        if self._repository.get_customer_by_id(data.customer_id) is None:
            raise OrderCustomerNotFoundError

        product_ids = {item.product_id for item in data.items}
        products = self._repository.get_products_by_ids(product_ids)
        products_by_id = {product.id: product for product in products}
        if products_by_id.keys() != product_ids:
            raise OrderProductNotFoundError
        if any(not product.is_active for product in products):
            raise InactiveOrderProductError

        persisted_items: list[tuple[int, int, Decimal]] = []
        total_amount = Decimal("0.00")
        for item in data.items:
            product = products_by_id[item.product_id]
            persisted_items.append((product.id, item.quantity, product.price))
            total_amount += product.price * item.quantity

        if total_amount > MAX_ORDER_TOTAL:
            raise OrderTotalOutOfRangeError

        order = self._repository.create(
            customer_id=data.customer_id,
            total_amount=total_amount,
            items=persisted_items,
        )
        return self._to_response(order)

    def list(self, *, page: int, page_size: int) -> OrderListResponse:
        orders, total = self._repository.list_page(
            offset=(page - 1) * page_size,
            limit=page_size,
        )
        pages = (total + page_size - 1) // page_size
        return OrderListResponse(
            items=[self._to_response(order) for order in orders],
            total=total,
            page=page,
            page_size=page_size,
            pages=pages,
        )

    def get(self, order_id: int) -> OrderResponse:
        order = self._repository.get_by_id(order_id)
        if order is None:
            raise OrderNotFoundError
        return self._to_response(order)

    def update(self, order_id: int, data: OrderUpdate) -> OrderResponse:
        order = self._repository.get_by_id(order_id)
        if order is None:
            raise OrderNotFoundError
        return self._to_response(self._repository.update_status(order, data.status))

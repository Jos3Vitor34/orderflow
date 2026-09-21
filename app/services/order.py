import logging
from collections.abc import Callable
from decimal import Decimal

from app.models.order import Order, OrderStatus
from app.repositories.order import OrderRepository
from app.schemas.order import (
    OrderCreate,
    OrderItemResponse,
    OrderListResponse,
    OrderResponse,
    OrderUpdate,
)

MAX_ORDER_TOTAL = Decimal("9999999999.99")
logger = logging.getLogger(__name__)


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


class InvalidOrderStatusTransitionError(Exception):
    """Raised when an Order status transition violates its lifecycle."""


class InsufficientOrderStockError(Exception):
    """Raised when an Order cannot be fulfilled from current locked stock."""


class InvalidOrderItemQuantityError(Exception):
    """Raised when persisted OrderItem data cannot be fulfilled safely."""


class UnavailableOrderItemProductError(Exception):
    """Raised when a persisted OrderItem no longer has an available Product."""


ALLOWED_ORDER_STATUS_TRANSITIONS: dict[OrderStatus, frozenset[OrderStatus]] = {
    OrderStatus.PENDING: frozenset({OrderStatus.PROCESSING, OrderStatus.CANCELLED}),
    OrderStatus.PROCESSING: frozenset({OrderStatus.CONFIRMED}),
    OrderStatus.CONFIRMED: frozenset({OrderStatus.SHIPPED}),
    OrderStatus.SHIPPED: frozenset({OrderStatus.DELIVERED}),
    OrderStatus.DELIVERED: frozenset(),
    OrderStatus.CANCELLED: frozenset(),
}


def validate_order_status_transition(
    current_status: OrderStatus,
    new_status: OrderStatus,
) -> None:
    if current_status == new_status:
        return
    if new_status not in ALLOWED_ORDER_STATUS_TRANSITIONS[current_status]:
        raise InvalidOrderStatusTransitionError


class OrderService:
    def __init__(
        self,
        repository: OrderRepository,
        *,
        confirmation_publisher: Callable[[int], object] | None = None,
    ) -> None:
        self._repository = repository
        self._confirmation_publisher = confirmation_publisher

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
        order = self._repository.get_by_id_for_update(order_id)
        if order is None:
            self._repository.rollback()
            raise OrderNotFoundError
        try:
            validate_order_status_transition(order.status, data.status)
            if order.status == data.status:
                self._repository.rollback()
                return self._to_response(order)

            if (
                order.status == OrderStatus.PROCESSING
                and data.status == OrderStatus.CONFIRMED
            ):
                self._debit_stock_for_confirmation(order)

            persisted = self._repository.update_status(order, data.status)
        except Exception:
            self._repository.rollback()
            raise
        if (
            self._confirmation_publisher is not None
            and data.status == OrderStatus.CONFIRMED
        ):
            try:
                self._confirmation_publisher(persisted.id)
            except Exception:
                logger.error(
                    "Order confirmation task publication failed after commit",
                    extra={"order_id": persisted.id},
                )
        return self._to_response(persisted)

    def _debit_stock_for_confirmation(self, order: Order) -> None:
        if not order.items or any(item.quantity <= 0 for item in order.items):
            raise InvalidOrderItemQuantityError

        product_ids = {item.product_id for item in order.items}
        products = self._repository.get_products_by_ids_for_update(product_ids)
        products_by_id = {product.id: product for product in products}
        if products_by_id.keys() != product_ids:
            raise UnavailableOrderItemProductError
        if any(not product.is_active for product in products):
            raise InactiveOrderProductError

        for item in order.items:
            if products_by_id[item.product_id].stock < item.quantity:
                raise InsufficientOrderStockError

        for item in order.items:
            products_by_id[item.product_id].stock -= item.quantity

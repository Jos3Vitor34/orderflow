from collections.abc import Generator
from datetime import UTC, datetime
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient

from app.api.dependencies import get_current_user, get_order_service
from app.main import app
from app.models.customer import Customer
from app.models.order import Order, OrderStatus
from app.models.order_item import OrderItem
from app.models.product import Product
from app.models.user import User
from app.repositories.order import OrderPersistenceError
from app.services.order import (
    InvalidOrderStatusTransitionError,
    OrderService,
    validate_order_status_transition,
)


class InMemoryOrderRepository:
    def __init__(self) -> None:
        self.customers: dict[int, Customer] = {}
        self.products: dict[int, Product] = {}
        self.orders: dict[int, Order] = {}
        self.create_calls = 0
        self.fail_next_create = False
        self.fail_next_update = False
        self.rollback_calls = 0
        self.order_lock_calls: list[int] = []
        self.product_lock_calls: list[list[int]] = []
        self._stock_snapshot: dict[int, int] = {}
        self._next_order_id = 1
        self._next_item_id = 1

    def get_customer_by_id(self, customer_id: int) -> Customer | None:
        return self.customers.get(customer_id)

    def get_products_by_ids(self, product_ids: set[int]) -> list[Product]:
        return [
            self.products[product_id]
            for product_id in sorted(product_ids)
            if product_id in self.products
        ]

    def create(
        self,
        *,
        customer_id: int,
        total_amount: Decimal,
        items: list[tuple[int, int, Decimal]],
    ) -> Order:
        self.create_calls += 1
        if self.fail_next_create:
            self.fail_next_create = False
            raise OrderPersistenceError

        now = datetime.now(UTC)
        order = Order(
            id=self._next_order_id,
            customer_id=customer_id,
            status=OrderStatus.PENDING,
            total_amount=total_amount,
            created_at=now,
            updated_at=now,
        )
        order.items = []
        for product_id, quantity, unit_price in items:
            order.items.append(
                OrderItem(
                    id=self._next_item_id,
                    order_id=order.id,
                    product_id=product_id,
                    quantity=quantity,
                    unit_price=unit_price,
                )
            )
            self._next_item_id += 1
        self.orders[order.id] = order
        self._next_order_id += 1
        return order

    def get_by_id(self, order_id: int) -> Order | None:
        return self.orders.get(order_id)

    def get_by_id_for_update(self, order_id: int) -> Order | None:
        self.order_lock_calls.append(order_id)
        return self.orders.get(order_id)

    def get_products_by_ids_for_update(
        self,
        product_ids: set[int],
    ) -> list[Product]:
        ordered_ids = sorted(product_ids)
        self.product_lock_calls.append(ordered_ids)
        products = [
            self.products[product_id]
            for product_id in ordered_ids
            if product_id in self.products
        ]
        self._stock_snapshot = {product.id: product.stock for product in products}
        return products

    def list_page(self, *, offset: int, limit: int) -> tuple[list[Order], int]:
        orders = sorted(self.orders.values(), key=lambda order: order.id)
        return orders[offset : offset + limit], len(orders)

    def update_status(self, order: Order, new_status: OrderStatus) -> Order:
        if self.fail_next_update:
            self.fail_next_update = False
            raise OrderPersistenceError
        order.status = new_status
        order.updated_at = datetime.now(UTC)
        self._stock_snapshot = {}
        return order

    def rollback(self) -> None:
        self.rollback_calls += 1
        for product_id, stock in self._stock_snapshot.items():
            self.products[product_id].stock = stock
        self._stock_snapshot = {}


@pytest.fixture
def order_context() -> Generator[tuple[TestClient, InMemoryOrderRepository]]:
    repository = InMemoryOrderRepository()
    now = datetime.now(UTC)
    repository.customers[1] = Customer(
        id=1,
        name="Order Customer",
        email="customer@example.com",
        phone=None,
        created_at=now,
        updated_at=now,
    )
    for product_id, sku, price, stock, is_active in [
        (10, "PRODUCT-A", Decimal("19.90"), 0, True),
        (20, "PRODUCT-B", Decimal("0.01"), 3, True),
        (30, "PRODUCT-C", Decimal("199.99"), 1, True),
        (40, "INACTIVE", Decimal("5.00"), 10, False),
        (50, "MAX-PRICE", Decimal("9999999999.99"), 10, True),
    ]:
        repository.products[product_id] = Product(
            id=product_id,
            sku=sku,
            name=sku,
            description=None,
            price=price,
            stock=stock,
            is_active=is_active,
            created_at=now,
            updated_at=now,
        )

    service = OrderService(repository)  # type: ignore[arg-type]
    authenticated_user = User(
        id=1,
        full_name="Authenticated User",
        email="user@example.com",
        hashed_password="not-used",
        is_active=True,
        created_at=now,
        updated_at=now,
    )
    app.dependency_overrides[get_order_service] = lambda: service
    app.dependency_overrides[get_current_user] = lambda: authenticated_user

    with TestClient(app) as client:
        yield client, repository

    app.dependency_overrides.pop(get_order_service, None)
    app.dependency_overrides.pop(get_current_user, None)


def valid_order_payload() -> dict[str, object]:
    return {
        "customer_id": 1,
        "items": [
            {"product_id": 10, "quantity": 2},
            {"product_id": 20, "quantity": 3},
            {"product_id": 30, "quantity": 1},
        ],
    }


def create_order(client: TestClient, payload: dict[str, object] | None = None) -> dict:
    response = client.post("/api/v1/orders", json=payload or valid_order_payload())
    assert response.status_code == 201
    return response.json()


def test_orders_require_authentication() -> None:
    with TestClient(app) as client:
        response = client.get("/api/v1/orders")

    assert response.status_code == 401
    assert response.headers["www-authenticate"] == "Bearer"


def test_create_order_calculates_total_and_server_controlled_price_snapshot(
    order_context: tuple[TestClient, InMemoryOrderRepository],
) -> None:
    client, repository = order_context

    body = create_order(client)

    assert body["customer_id"] == 1
    assert body["status"] == "pending"
    assert body["total_amount"] == "239.82"
    assert [item["unit_price"] for item in body["items"]] == [
        "19.90",
        "0.01",
        "199.99",
    ]
    assert [item["quantity"] for item in body["items"]] == [2, 3, 1]
    assert repository.orders[body["id"]].total_amount == Decimal("239.82")


def test_create_order_does_not_change_stock_without_a_lifecycle_rule(
    order_context: tuple[TestClient, InMemoryOrderRepository],
) -> None:
    client, repository = order_context
    stock_before = {
        product_id: product.stock for product_id, product in repository.products.items()
    }

    create_order(client)

    assert {
        product_id: product.stock for product_id, product in repository.products.items()
    } == stock_before


def test_order_price_snapshot_survives_later_product_price_change(
    order_context: tuple[TestClient, InMemoryOrderRepository],
) -> None:
    client, repository = order_context
    created = create_order(
        client,
        {"customer_id": 1, "items": [{"product_id": 10, "quantity": 1}]},
    )
    repository.products[10].price = Decimal("29.90")

    response = client.get(f"/api/v1/orders/{created['id']}")

    assert response.status_code == 200
    assert response.json()["items"][0]["unit_price"] == "19.90"
    assert response.json()["total_amount"] == "19.90"


def test_create_order_rejects_missing_customer_without_persistence(
    order_context: tuple[TestClient, InMemoryOrderRepository],
) -> None:
    client, repository = order_context
    payload = valid_order_payload() | {"customer_id": 999}

    response = client.post("/api/v1/orders", json=payload)

    assert response.status_code == 404
    assert repository.orders == {}
    assert repository.create_calls == 0


def test_create_order_with_missing_product_is_atomic(
    order_context: tuple[TestClient, InMemoryOrderRepository],
) -> None:
    client, repository = order_context
    payload = {
        "customer_id": 1,
        "items": [
            {"product_id": 10, "quantity": 1},
            {"product_id": 999, "quantity": 1},
        ],
    }

    response = client.post("/api/v1/orders", json=payload)

    assert response.status_code == 404
    assert repository.orders == {}
    assert repository.create_calls == 0


def test_create_order_rejects_inactive_product(
    order_context: tuple[TestClient, InMemoryOrderRepository],
) -> None:
    client, repository = order_context

    response = client.post(
        "/api/v1/orders",
        json={"customer_id": 1, "items": [{"product_id": 40, "quantity": 1}]},
    )

    assert response.status_code == 409
    assert repository.orders == {}


@pytest.mark.parametrize(
    "payload",
    [
        {"customer_id": 1, "items": []},
        {"customer_id": 1, "items": [{"product_id": 10, "quantity": 0}]},
        {"customer_id": 1, "items": [{"product_id": 10, "quantity": -1}]},
        {"customer_id": 1, "items": [{"product_id": 10, "quantity": None}]},
        {"customer_id": 1, "items": [{"product_id": 10, "quantity": 1.5}]},
        {
            "customer_id": 1,
            "items": [
                {"product_id": 10, "quantity": 1},
                {"product_id": 10, "quantity": 2},
            ],
        },
        {
            "customer_id": 1,
            "items": [{"product_id": 10, "quantity": 1, "unit_price": "0.01"}],
        },
        {
            "customer_id": 1,
            "items": [{"product_id": 10, "quantity": 1}],
            "total_amount": "0.01",
        },
        {
            "customer_id": 1,
            "items": [{"product_id": 10, "quantity": 1}],
            "status": "confirmed",
        },
        {"customer_id": 1, "items": [{"product_id": 10, "quantity": 1}], "x": 1},
    ],
)
def test_create_order_rejects_invalid_payload(
    order_context: tuple[TestClient, InMemoryOrderRepository],
    payload: dict[str, object],
) -> None:
    client, repository = order_context

    response = client.post("/api/v1/orders", json=payload)

    assert response.status_code == 422
    assert repository.orders == {}


def test_create_order_rejects_total_outside_numeric_precision(
    order_context: tuple[TestClient, InMemoryOrderRepository],
) -> None:
    client, repository = order_context

    response = client.post(
        "/api/v1/orders",
        json={"customer_id": 1, "items": [{"product_id": 50, "quantity": 2}]},
    )

    assert response.status_code == 422
    assert repository.orders == {}


def test_repository_failure_does_not_leave_partial_order_and_service_recovers(
    order_context: tuple[TestClient, InMemoryOrderRepository],
) -> None:
    client, repository = order_context
    repository.fail_next_create = True

    failed_response = client.post("/api/v1/orders", json=valid_order_payload())
    successful_response = client.post("/api/v1/orders", json=valid_order_payload())

    assert failed_response.status_code == 409
    assert successful_response.status_code == 201
    assert len(repository.orders) == 1


def test_list_orders_is_empty(
    order_context: tuple[TestClient, InMemoryOrderRepository],
) -> None:
    client, _ = order_context

    response = client.get("/api/v1/orders")

    assert response.status_code == 200
    assert response.json() == {
        "items": [],
        "total": 0,
        "page": 1,
        "page_size": 20,
        "pages": 0,
    }


def test_list_orders_uses_deterministic_pagination_with_nested_items(
    order_context: tuple[TestClient, InMemoryOrderRepository],
) -> None:
    client, _ = order_context
    for _ in range(5):
        create_order(
            client,
            {"customer_id": 1, "items": [{"product_id": 20, "quantity": 1}]},
        )

    response = client.get("/api/v1/orders?page=2&page_size=2")

    assert response.status_code == 200
    body = response.json()
    assert [order["id"] for order in body["items"]] == [3, 4]
    assert all(len(order["items"]) == 1 for order in body["items"])
    assert body["total"] == 5
    assert body["page"] == 2
    assert body["page_size"] == 2
    assert body["pages"] == 3


@pytest.mark.parametrize("query", ["page=0", "page_size=0", "page_size=101"])
def test_list_orders_validates_pagination_limits(
    order_context: tuple[TestClient, InMemoryOrderRepository],
    query: str,
) -> None:
    client, _ = order_context

    response = client.get(f"/api/v1/orders?{query}")

    assert response.status_code == 422


def test_get_existing_order_with_items(
    order_context: tuple[TestClient, InMemoryOrderRepository],
) -> None:
    client, _ = order_context
    created = create_order(client)

    response = client.get(f"/api/v1/orders/{created['id']}")

    assert response.status_code == 200
    assert response.json() == created


def test_get_missing_order_returns_404(
    order_context: tuple[TestClient, InMemoryOrderRepository],
) -> None:
    client, _ = order_context

    response = client.get("/api/v1/orders/999")

    assert response.status_code == 404


def test_patch_order_applies_the_valid_lifecycle_without_changing_snapshots(
    order_context: tuple[TestClient, InMemoryOrderRepository],
) -> None:
    client, repository = order_context
    created = create_order(
        client,
        {
            "customer_id": 1,
            "items": [
                {"product_id": 20, "quantity": 2},
                {"product_id": 30, "quantity": 1},
            ],
        },
    )

    responses = []
    for target in ["processing", "confirmed", "shipped", "delivered"]:
        responses.append(
            client.patch(
                f"/api/v1/orders/{created['id']}",
                json={"status": target},
            )
        )

    assert [response.status_code for response in responses] == [200, 200, 200, 200]
    assert responses[-1].json()["status"] == "delivered"
    assert responses[-1].json()["customer_id"] == created["customer_id"]
    assert responses[-1].json()["total_amount"] == created["total_amount"]
    assert responses[-1].json()["items"] == created["items"]
    assert repository.products[20].stock == 1
    assert repository.products[30].stock == 0


@pytest.mark.parametrize(
    ("current", "target"),
    [
        (OrderStatus.PENDING, OrderStatus.PROCESSING),
        (OrderStatus.PROCESSING, OrderStatus.CONFIRMED),
        (OrderStatus.CONFIRMED, OrderStatus.SHIPPED),
        (OrderStatus.SHIPPED, OrderStatus.DELIVERED),
        (OrderStatus.PENDING, OrderStatus.CANCELLED),
    ],
)
def test_order_state_machine_accepts_only_declared_edges(
    current: OrderStatus,
    target: OrderStatus,
) -> None:
    validate_order_status_transition(current, target)


@pytest.mark.parametrize(
    ("current", "target"),
    [
        (OrderStatus.PENDING, OrderStatus.CONFIRMED),
        (OrderStatus.PENDING, OrderStatus.SHIPPED),
        (OrderStatus.PROCESSING, OrderStatus.SHIPPED),
        (OrderStatus.PROCESSING, OrderStatus.CANCELLED),
        (OrderStatus.CONFIRMED, OrderStatus.PROCESSING),
        (OrderStatus.CONFIRMED, OrderStatus.CANCELLED),
        (OrderStatus.SHIPPED, OrderStatus.CONFIRMED),
        (OrderStatus.SHIPPED, OrderStatus.CANCELLED),
        (OrderStatus.DELIVERED, OrderStatus.PENDING),
        (OrderStatus.CANCELLED, OrderStatus.PENDING),
    ],
)
def test_order_state_machine_rejects_skips_regressions_and_terminal_changes(
    current: OrderStatus,
    target: OrderStatus,
) -> None:
    with pytest.raises(InvalidOrderStatusTransitionError):
        validate_order_status_transition(current, target)


@pytest.mark.parametrize("status_value", list(OrderStatus))
def test_order_state_machine_treats_same_state_as_idempotent(
    status_value: OrderStatus,
) -> None:
    validate_order_status_transition(status_value, status_value)


def test_pending_to_processing_and_pending_to_cancelled_do_not_move_stock(
    order_context: tuple[TestClient, InMemoryOrderRepository],
) -> None:
    client, repository = order_context
    processing_order = create_order(
        client,
        {"customer_id": 1, "items": [{"product_id": 20, "quantity": 2}]},
    )
    cancelled_order = create_order(
        client,
        {"customer_id": 1, "items": [{"product_id": 30, "quantity": 1}]},
    )
    stock_before = {key: product.stock for key, product in repository.products.items()}

    processing = client.patch(
        f"/api/v1/orders/{processing_order['id']}",
        json={"status": "processing"},
    )
    cancelled = client.patch(
        f"/api/v1/orders/{cancelled_order['id']}",
        json={"status": "cancelled"},
    )

    assert processing.status_code == 200
    assert cancelled.status_code == 200
    assert {key: product.stock for key, product in repository.products.items()} == (
        stock_before
    )
    assert repository.product_lock_calls == []


def test_confirmation_uses_persisted_quantities_and_exact_stock(
    order_context: tuple[TestClient, InMemoryOrderRepository],
) -> None:
    client, repository = order_context
    created = create_order(
        client,
        {
            "customer_id": 1,
            "items": [
                {"product_id": 20, "quantity": 3},
                {"product_id": 30, "quantity": 1},
            ],
        },
    )
    original_items = created["items"]
    assert (
        client.patch(
            f"/api/v1/orders/{created['id']}", json={"status": "processing"}
        ).status_code
        == 200
    )

    confirmed = client.patch(
        f"/api/v1/orders/{created['id']}", json={"status": "confirmed"}
    )

    assert confirmed.status_code == 200
    assert confirmed.json()["items"] == original_items
    assert repository.products[20].stock == 0
    assert repository.products[30].stock == 0
    assert repository.product_lock_calls == [[20, 30]]


def test_one_insufficient_item_rejects_entire_confirmation_and_recovers(
    order_context: tuple[TestClient, InMemoryOrderRepository],
) -> None:
    client, repository = order_context
    created = create_order(
        client,
        {
            "customer_id": 1,
            "items": [
                {"product_id": 20, "quantity": 2},
                {"product_id": 30, "quantity": 2},
            ],
        },
    )
    client.patch(f"/api/v1/orders/{created['id']}", json={"status": "processing"})
    stock_before = {
        20: repository.products[20].stock,
        30: repository.products[30].stock,
    }

    failed = client.patch(
        f"/api/v1/orders/{created['id']}", json={"status": "confirmed"}
    )
    assert failed.status_code == 409
    assert repository.orders[created["id"]].status == OrderStatus.PROCESSING
    assert {
        20: repository.products[20].stock,
        30: repository.products[30].stock,
    } == stock_before

    repository.products[30].stock = 2
    recovered = client.patch(
        f"/api/v1/orders/{created['id']}", json={"status": "confirmed"}
    )

    assert repository.orders[created["id"]].status == OrderStatus.CONFIRMED
    assert repository.products[20].stock == 1
    assert repository.products[30].stock == 0
    assert recovered.status_code == 200


def test_missing_or_inactive_persisted_product_rejects_confirmation(
    order_context: tuple[TestClient, InMemoryOrderRepository],
) -> None:
    client, repository = order_context
    missing = create_order(
        client,
        {"customer_id": 1, "items": [{"product_id": 20, "quantity": 1}]},
    )
    inactive = create_order(
        client,
        {"customer_id": 1, "items": [{"product_id": 30, "quantity": 1}]},
    )
    for order in (missing, inactive):
        client.patch(f"/api/v1/orders/{order['id']}", json={"status": "processing"})
    repository.products.pop(20)
    repository.products[30].is_active = False

    missing_response = client.patch(
        f"/api/v1/orders/{missing['id']}", json={"status": "confirmed"}
    )
    inactive_response = client.patch(
        f"/api/v1/orders/{inactive['id']}", json={"status": "confirmed"}
    )

    assert missing_response.status_code == 409
    assert inactive_response.status_code == 409
    assert repository.orders[missing["id"]].status == OrderStatus.PROCESSING
    assert repository.orders[inactive["id"]].status == OrderStatus.PROCESSING


def test_invalid_persisted_item_quantity_rejects_confirmation_without_stock_change(
    order_context: tuple[TestClient, InMemoryOrderRepository],
) -> None:
    client, repository = order_context
    created = create_order(
        client,
        {"customer_id": 1, "items": [{"product_id": 20, "quantity": 1}]},
    )
    client.patch(f"/api/v1/orders/{created['id']}", json={"status": "processing"})
    repository.orders[created["id"]].items[0].quantity = 0

    response = client.patch(
        f"/api/v1/orders/{created['id']}", json={"status": "confirmed"}
    )

    assert response.status_code == 409
    assert repository.orders[created["id"]].status == OrderStatus.PROCESSING
    assert repository.products[20].stock == 3


def test_invalid_transition_and_repeated_confirmation_never_move_stock_twice(
    order_context: tuple[TestClient, InMemoryOrderRepository],
) -> None:
    client, repository = order_context
    created = create_order(
        client,
        {"customer_id": 1, "items": [{"product_id": 20, "quantity": 1}]},
    )
    invalid = client.patch(
        f"/api/v1/orders/{created['id']}", json={"status": "confirmed"}
    )
    client.patch(f"/api/v1/orders/{created['id']}", json={"status": "processing"})
    first = client.patch(
        f"/api/v1/orders/{created['id']}", json={"status": "confirmed"}
    )
    stock_after_first = repository.products[20].stock
    repeated = client.patch(
        f"/api/v1/orders/{created['id']}", json={"status": "confirmed"}
    )

    assert invalid.status_code == 409
    assert first.status_code == 200
    assert repeated.status_code == 200
    assert repository.products[20].stock == stock_after_first == 2
    assert repository.product_lock_calls == [[20]]


def test_shipped_and_delivered_transitions_do_not_move_stock(
    order_context: tuple[TestClient, InMemoryOrderRepository],
) -> None:
    client, repository = order_context
    created = create_order(
        client,
        {"customer_id": 1, "items": [{"product_id": 20, "quantity": 1}]},
    )
    for target in ("processing", "confirmed"):
        client.patch(f"/api/v1/orders/{created['id']}", json={"status": target})
    stock_after_confirmation = repository.products[20].stock

    shipped = client.patch(
        f"/api/v1/orders/{created['id']}", json={"status": "shipped"}
    )
    delivered = client.patch(
        f"/api/v1/orders/{created['id']}", json={"status": "delivered"}
    )

    assert shipped.status_code == 200
    assert delivered.status_code == 200
    assert repository.products[20].stock == stock_after_confirmation
    assert repository.product_lock_calls == [[20]]


def test_update_failure_rolls_back_stock_and_session_is_reusable(
    order_context: tuple[TestClient, InMemoryOrderRepository],
) -> None:
    client, repository = order_context
    created = create_order(
        client,
        {"customer_id": 1, "items": [{"product_id": 20, "quantity": 2}]},
    )
    client.patch(f"/api/v1/orders/{created['id']}", json={"status": "processing"})
    repository.fail_next_update = True

    failed = client.patch(
        f"/api/v1/orders/{created['id']}", json={"status": "confirmed"}
    )
    recovered = client.patch(
        f"/api/v1/orders/{created['id']}", json={"status": "confirmed"}
    )

    assert failed.status_code == 409
    assert recovered.status_code == 200
    assert repository.products[20].stock == 1


@pytest.mark.parametrize(
    "payload",
    [
        {"status": "unknown"},
        {"status": None},
        {},
        {"customer_id": 2},
        {"total_amount": "0.01"},
        {"items": []},
    ],
)
def test_patch_order_rejects_invalid_or_immutable_fields(
    order_context: tuple[TestClient, InMemoryOrderRepository],
    payload: dict[str, object],
) -> None:
    client, _ = order_context
    created = create_order(client)

    response = client.patch(f"/api/v1/orders/{created['id']}", json=payload)

    assert response.status_code == 422


def test_patch_missing_order_returns_404(
    order_context: tuple[TestClient, InMemoryOrderRepository],
) -> None:
    client, _ = order_context

    response = client.patch("/api/v1/orders/999", json={"status": "cancelled"})

    assert response.status_code == 404


def test_order_openapi_documents_aggregate_without_delete() -> None:
    schema = app.openapi()
    collection = schema["paths"]["/api/v1/orders"]
    resource = schema["paths"]["/api/v1/orders/{order_id}"]

    assert set(collection) == {"get", "post"}
    assert set(resource) == {"get", "patch"}
    for operation in [*collection.values(), *resource.values()]:
        assert operation["security"] == [{"OAuth2PasswordBearer": []}]

    assert {"201", "401", "404", "409", "422"} <= set(collection["post"]["responses"])
    assert {"200", "401", "404", "422"} <= set(resource["get"]["responses"])
    assert {"200", "401", "404", "409", "422"} <= set(resource["patch"]["responses"])
    assert {
        "OrderCreate",
        "OrderItemCreate",
        "OrderItemResponse",
        "OrderListResponse",
        "OrderResponse",
        "OrderUpdate",
        "OrderStatus",
    } <= set(schema["components"]["schemas"])

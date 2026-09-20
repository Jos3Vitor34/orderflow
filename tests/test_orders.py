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
from app.services.order import OrderService


class InMemoryOrderRepository:
    def __init__(self) -> None:
        self.customers: dict[int, Customer] = {}
        self.products: dict[int, Product] = {}
        self.orders: dict[int, Order] = {}
        self.create_calls = 0
        self.fail_next_create = False
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

    def list_page(self, *, offset: int, limit: int) -> tuple[list[Order], int]:
        orders = sorted(self.orders.values(), key=lambda order: order.id)
        return orders[offset : offset + limit], len(orders)

    def update_status(self, order: Order, new_status: OrderStatus) -> Order:
        order.status = new_status
        order.updated_at = datetime.now(UTC)
        return order


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


def test_patch_order_allows_only_declared_status(
    order_context: tuple[TestClient, InMemoryOrderRepository],
) -> None:
    client, _ = order_context
    created = create_order(client)

    response = client.patch(
        f"/api/v1/orders/{created['id']}",
        json={"status": "confirmed"},
    )

    assert response.status_code == 200
    assert response.json()["status"] == "confirmed"
    assert response.json()["customer_id"] == created["customer_id"]
    assert response.json()["total_amount"] == created["total_amount"]
    assert response.json()["items"] == created["items"]


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

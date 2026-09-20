from collections.abc import Generator
from datetime import UTC, datetime
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient

from app.api.dependencies import get_current_user, get_payment_service
from app.main import app
from app.models.order import Order, OrderStatus
from app.models.payment import Payment, PaymentStatus
from app.models.user import User
from app.repositories.payment import (
    DuplicatePaymentReferenceError,
    PaymentConstraintError,
)
from app.services.payment import PaymentService


class InMemoryPaymentRepository:
    def __init__(self) -> None:
        self.orders: dict[int, Order] = {}
        self.payments: dict[int, Payment] = {}
        self.create_calls = 0
        self.fail_next_create = False
        self._next_id = 1

    def get_order_for_payment(self, order_id: int) -> Order | None:
        return self.orders.get(order_id)

    def get_by_id(self, payment_id: int) -> Payment | None:
        return self.payments.get(payment_id)

    def get_by_id_for_update(self, payment_id: int) -> Payment | None:
        return self.get_by_id(payment_id)

    def get_by_provider_reference(self, provider_reference: str) -> Payment | None:
        return next(
            (
                payment
                for payment in self.payments.values()
                if payment.provider_reference == provider_reference
            ),
            None,
        )

    def create(
        self,
        *,
        order_id: int,
        provider: str,
        provider_reference: str | None,
        amount: Decimal,
    ) -> Payment:
        self.create_calls += 1
        if self.fail_next_create:
            self.fail_next_create = False
            raise PaymentConstraintError
        if (
            provider_reference is not None
            and self.get_by_provider_reference(provider_reference) is not None
        ):
            raise DuplicatePaymentReferenceError

        now = datetime.now(UTC)
        payment = Payment(
            id=self._next_id,
            order_id=order_id,
            provider=provider,
            provider_reference=provider_reference,
            amount=amount,
            status=PaymentStatus.PENDING,
            created_at=now,
            updated_at=now,
        )
        self.payments[payment.id] = payment
        self._next_id += 1
        return payment

    def list_page(self, *, offset: int, limit: int) -> tuple[list[Payment], int]:
        payments = sorted(self.payments.values(), key=lambda payment: payment.id)
        return payments[offset : offset + limit], len(payments)

    def update_status(self, payment: Payment, new_status: PaymentStatus) -> Payment:
        payment.status = new_status
        payment.updated_at = datetime.now(UTC)
        return payment


@pytest.fixture
def payment_context() -> Generator[tuple[TestClient, InMemoryPaymentRepository]]:
    repository = InMemoryPaymentRepository()
    now = datetime.now(UTC)
    for order_id, status, total in [
        (1, OrderStatus.PENDING, Decimal("239.82")),
        (2, OrderStatus.CONFIRMED, Decimal("19.90")),
        (3, OrderStatus.CANCELLED, Decimal("199.99")),
        (4, OrderStatus.PENDING, Decimal("0.00")),
    ]:
        repository.orders[order_id] = Order(
            id=order_id,
            customer_id=1,
            status=status,
            total_amount=total,
            created_at=now,
            updated_at=now,
        )

    service = PaymentService(repository)  # type: ignore[arg-type]
    authenticated_user = User(
        id=1,
        full_name="Authenticated User",
        email="user@example.com",
        hashed_password="not-used",
        is_active=True,
        created_at=now,
        updated_at=now,
    )
    app.dependency_overrides[get_payment_service] = lambda: service
    app.dependency_overrides[get_current_user] = lambda: authenticated_user

    with TestClient(app) as client:
        yield client, repository

    app.dependency_overrides.pop(get_payment_service, None)
    app.dependency_overrides.pop(get_current_user, None)


def create_payment(
    client: TestClient,
    *,
    order_id: int = 1,
    provider: str = "manual",
    provider_reference: str | None = "payment-001",
) -> dict:
    payload: dict[str, object] = {"order_id": order_id, "provider": provider}
    if provider_reference is not None:
        payload["provider_reference"] = provider_reference
    response = client.post("/api/v1/payments", json=payload)
    assert response.status_code == 201
    return response.json()


@pytest.mark.parametrize(
    ("method", "path", "json"),
    [
        ("post", "/api/v1/payments", {"order_id": 1, "provider": "manual"}),
        ("get", "/api/v1/payments", None),
        ("get", "/api/v1/payments/1", None),
        ("patch", "/api/v1/payments/1", {"status": "approved"}),
    ],
)
def test_payment_endpoints_require_authentication(
    method: str,
    path: str,
    json: dict[str, object] | None,
) -> None:
    with TestClient(app) as client:
        response = client.request(method, path, json=json)

    assert response.status_code == 401
    assert response.headers["www-authenticate"] == "Bearer"


@pytest.mark.parametrize("amount", ["0.01", "19.90", "239.82", "199.99"])
def test_create_payment_uses_exact_order_total_as_server_snapshot(
    payment_context: tuple[TestClient, InMemoryPaymentRepository],
    amount: str,
) -> None:
    client, repository = payment_context
    repository.orders[1].total_amount = Decimal(amount)

    body = create_payment(client)

    assert body["amount"] == amount
    assert body["status"] == "pending"
    saved = repository.payments[body["id"]]
    assert saved.amount == Decimal(amount)
    assert isinstance(saved.amount, Decimal)


def test_payment_amount_remains_snapshot_after_order_total_changes(
    payment_context: tuple[TestClient, InMemoryPaymentRepository],
) -> None:
    client, repository = payment_context
    created = create_payment(client)
    repository.orders[1].total_amount = Decimal("999.99")

    response = client.get(f"/api/v1/payments/{created['id']}")

    assert response.status_code == 200
    assert response.json()["amount"] == "239.82"


@pytest.mark.parametrize(
    "payload",
    [
        {},
        {"order_id": 1},
        {"provider": "manual"},
        {"order_id": 0, "provider": "manual"},
        {"order_id": 1, "provider": ""},
        {"order_id": 1, "provider": "manual", "provider_reference": ""},
        {"order_id": 1, "provider": "manual", "amount": "0.01"},
        {"order_id": 1, "provider": "manual", "status": "approved"},
        {"order_id": 1, "provider": "manual", "unknown": True},
    ],
)
def test_create_payment_rejects_invalid_or_server_controlled_fields(
    payment_context: tuple[TestClient, InMemoryPaymentRepository],
    payload: dict[str, object],
) -> None:
    client, repository = payment_context

    response = client.post("/api/v1/payments", json=payload)

    assert response.status_code == 422
    assert repository.payments == {}


def test_create_payment_rejects_missing_order_without_persistence(
    payment_context: tuple[TestClient, InMemoryPaymentRepository],
) -> None:
    client, repository = payment_context

    response = client.post(
        "/api/v1/payments",
        json={"order_id": 999, "provider": "manual"},
    )

    assert response.status_code == 404
    assert repository.payments == {}
    assert repository.create_calls == 0


def test_create_payment_rejects_cancelled_and_zero_total_orders(
    payment_context: tuple[TestClient, InMemoryPaymentRepository],
) -> None:
    client, repository = payment_context

    cancelled = client.post(
        "/api/v1/payments", json={"order_id": 3, "provider": "manual"}
    )
    zero_total = client.post(
        "/api/v1/payments", json={"order_id": 4, "provider": "manual"}
    )

    assert cancelled.status_code == 409
    assert zero_total.status_code == 409
    assert repository.payments == {}


def test_provider_reference_is_idempotent_for_same_structural_request(
    payment_context: tuple[TestClient, InMemoryPaymentRepository],
) -> None:
    client, repository = payment_context

    first = create_payment(client)
    second = create_payment(client)

    assert second == first
    assert len(repository.payments) == 1
    assert repository.create_calls == 1


def test_provider_reference_conflicts_with_different_structural_request(
    payment_context: tuple[TestClient, InMemoryPaymentRepository],
) -> None:
    client, repository = payment_context
    create_payment(client)

    response = client.post(
        "/api/v1/payments",
        json={
            "order_id": 2,
            "provider": "manual",
            "provider_reference": "payment-001",
        },
    )

    assert response.status_code == 409
    assert len(repository.payments) == 1


def test_schema_allows_multiple_payments_for_an_order(
    payment_context: tuple[TestClient, InMemoryPaymentRepository],
) -> None:
    client, repository = payment_context

    first = create_payment(client, provider_reference=None)
    second = create_payment(client, provider_reference=None)

    assert first["id"] != second["id"]
    assert len(repository.payments) == 2


def test_repository_failure_is_atomic_and_a_later_request_succeeds(
    payment_context: tuple[TestClient, InMemoryPaymentRepository],
) -> None:
    client, repository = payment_context
    repository.fail_next_create = True

    failed = client.post(
        "/api/v1/payments",
        json={"order_id": 1, "provider": "manual"},
    )
    recovered = client.post(
        "/api/v1/payments",
        json={"order_id": 1, "provider": "manual"},
    )

    assert failed.status_code == 409
    assert recovered.status_code == 201
    assert len(repository.payments) == 1


def test_list_payments_is_empty(
    payment_context: tuple[TestClient, InMemoryPaymentRepository],
) -> None:
    client, _ = payment_context

    response = client.get("/api/v1/payments")

    assert response.status_code == 200
    assert response.json() == {
        "items": [],
        "total": 0,
        "page": 1,
        "page_size": 20,
        "pages": 0,
    }


def test_list_payments_uses_deterministic_pagination(
    payment_context: tuple[TestClient, InMemoryPaymentRepository],
) -> None:
    client, _ = payment_context
    for _ in range(5):
        create_payment(client, provider_reference=None)

    response = client.get("/api/v1/payments?page=2&page_size=2")

    assert response.status_code == 200
    body = response.json()
    assert [payment["id"] for payment in body["items"]] == [3, 4]
    assert body | {"items": []} == {
        "items": [],
        "total": 5,
        "page": 2,
        "page_size": 2,
        "pages": 3,
    }


@pytest.mark.parametrize("query", ["page=0", "page_size=0", "page_size=101"])
def test_list_payments_validates_pagination_limits(
    payment_context: tuple[TestClient, InMemoryPaymentRepository],
    query: str,
) -> None:
    client, _ = payment_context

    response = client.get(f"/api/v1/payments?{query}")

    assert response.status_code == 422


def test_get_payment_and_missing_payment(
    payment_context: tuple[TestClient, InMemoryPaymentRepository],
) -> None:
    client, _ = payment_context
    created = create_payment(client)

    found = client.get(f"/api/v1/payments/{created['id']}")
    missing = client.get("/api/v1/payments/999")

    assert found.status_code == 200
    assert found.json() == created
    assert missing.status_code == 404


def test_patch_payment_follows_minimal_financial_lifecycle(
    payment_context: tuple[TestClient, InMemoryPaymentRepository],
) -> None:
    client, _ = payment_context
    created = create_payment(client)

    approved = client.patch(
        f"/api/v1/payments/{created['id']}", json={"status": "approved"}
    )
    repeated = client.patch(
        f"/api/v1/payments/{created['id']}", json={"status": "approved"}
    )
    refunded = client.patch(
        f"/api/v1/payments/{created['id']}", json={"status": "refunded"}
    )

    assert approved.status_code == 200
    assert approved.json()["status"] == "approved"
    assert repeated.status_code == 200
    assert refunded.status_code == 200
    assert refunded.json()["status"] == "refunded"


@pytest.mark.parametrize(
    ("initial_status", "new_status"),
    [
        (PaymentStatus.PENDING, "refunded"),
        (PaymentStatus.APPROVED, "failed"),
        (PaymentStatus.FAILED, "approved"),
        (PaymentStatus.REFUNDED, "approved"),
    ],
)
def test_patch_payment_rejects_destructive_status_transitions(
    payment_context: tuple[TestClient, InMemoryPaymentRepository],
    initial_status: PaymentStatus,
    new_status: str,
) -> None:
    client, repository = payment_context
    created = create_payment(client)
    repository.payments[created["id"]].status = initial_status

    response = client.patch(
        f"/api/v1/payments/{created['id']}", json={"status": new_status}
    )

    assert response.status_code == 409
    assert repository.payments[created["id"]].status == initial_status


@pytest.mark.parametrize(
    "payload",
    [
        {},
        {"status": None},
        {"status": "unknown"},
        {"amount": "1.00"},
        {"order_id": 2},
        {"provider": "other"},
        {"provider_reference": "other"},
        {"id": 2},
        {"created_at": "2026-01-01T00:00:00Z"},
    ],
)
def test_patch_payment_rejects_invalid_or_immutable_fields(
    payment_context: tuple[TestClient, InMemoryPaymentRepository],
    payload: dict[str, object],
) -> None:
    client, _ = payment_context
    created = create_payment(client)

    response = client.patch(f"/api/v1/payments/{created['id']}", json=payload)

    assert response.status_code == 422


def test_patch_missing_payment_returns_404(
    payment_context: tuple[TestClient, InMemoryPaymentRepository],
) -> None:
    client, _ = payment_context

    response = client.patch("/api/v1/payments/999", json={"status": "approved"})

    assert response.status_code == 404


def test_payment_changes_do_not_mutate_order_status(
    payment_context: tuple[TestClient, InMemoryPaymentRepository],
) -> None:
    client, repository = payment_context
    created = create_payment(client)

    response = client.patch(
        f"/api/v1/payments/{created['id']}", json={"status": "approved"}
    )

    assert response.status_code == 200
    assert repository.orders[1].status == OrderStatus.PENDING


def test_payment_openapi_documents_security_contract_and_no_delete() -> None:
    schema = app.openapi()
    collection = schema["paths"]["/api/v1/payments"]
    resource = schema["paths"]["/api/v1/payments/{payment_id}"]

    assert set(collection) == {"get", "post"}
    assert set(resource) == {"get", "patch"}
    for operation in [*collection.values(), *resource.values()]:
        assert operation["security"] == [{"OAuth2PasswordBearer": []}]

    assert {"201", "401", "404", "409", "422"} <= set(collection["post"]["responses"])
    assert {"200", "401", "404", "422"} <= set(resource["get"]["responses"])
    assert {"200", "401", "404", "409", "422"} <= set(resource["patch"]["responses"])
    assert {
        "PaymentCreate",
        "PaymentListResponse",
        "PaymentResponse",
        "PaymentStatus",
        "PaymentUpdate",
    } <= set(schema["components"]["schemas"])

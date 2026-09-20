from collections.abc import Generator
from datetime import UTC, datetime

import pytest
from fastapi.testclient import TestClient

from app.api.dependencies import get_current_user, get_customer_service
from app.main import app
from app.models.customer import Customer
from app.models.user import User
from app.repositories.customer import DuplicateCustomerEmailError
from app.services.customer import CustomerService


class InMemoryCustomerRepository:
    def __init__(self) -> None:
        self.customers: dict[int, Customer] = {}

    def get_by_id(self, customer_id: int) -> Customer | None:
        return self.customers.get(customer_id)

    def get_by_email(self, email: str) -> Customer | None:
        return next(
            (
                customer
                for customer in self.customers.values()
                if customer.email.lower() == email
            ),
            None,
        )

    def create(self, *, name: str, email: str, phone: str | None) -> Customer:
        if self.get_by_email(email) is not None:
            raise DuplicateCustomerEmailError
        now = datetime.now(UTC)
        customer = Customer(
            id=len(self.customers) + 1,
            name=name,
            email=email,
            phone=phone,
            created_at=now,
            updated_at=now,
        )
        self.customers[customer.id] = customer
        return customer

    def list_page(self, *, offset: int, limit: int) -> tuple[list[Customer], int]:
        customers = sorted(self.customers.values(), key=lambda customer: customer.id)
        return customers[offset : offset + limit], len(customers)

    def update(self, customer: Customer, changes: dict[str, object]) -> Customer:
        if "email" in changes:
            existing = self.get_by_email(str(changes["email"]))
            if existing is not None and existing.id != customer.id:
                raise DuplicateCustomerEmailError
        for field, value in changes.items():
            setattr(customer, field, value)
        customer.updated_at = datetime.now(UTC)
        return customer

    def delete(self, customer: Customer) -> None:
        del self.customers[customer.id]


@pytest.fixture
def customer_context() -> Generator[tuple[TestClient, InMemoryCustomerRepository]]:
    repository = InMemoryCustomerRepository()
    service = CustomerService(repository)  # type: ignore[arg-type]
    now = datetime.now(UTC)
    authenticated_user = User(
        id=1,
        full_name="Authenticated User",
        email="user@example.com",
        hashed_password="not-used",
        is_active=True,
        created_at=now,
        updated_at=now,
    )
    app.dependency_overrides[get_customer_service] = lambda: service
    app.dependency_overrides[get_current_user] = lambda: authenticated_user

    with TestClient(app) as client:
        yield client, repository

    app.dependency_overrides.pop(get_customer_service, None)
    app.dependency_overrides.pop(get_current_user, None)


def create_customer(
    client: TestClient,
    *,
    name: str = "  Ada Lovelace  ",
    email: str = "Ada@Example.COM",
    phone: str | None = "+55 11 99999-0000",
) -> dict:
    response = client.post(
        "/api/v1/customers",
        json={"name": name, "email": email, "phone": phone},
    )
    assert response.status_code == 201
    return response.json()


def test_customers_require_authentication() -> None:
    with TestClient(app) as client:
        response = client.get("/api/v1/customers")

    assert response.status_code == 401
    assert response.headers["www-authenticate"] == "Bearer"


def test_create_customer_with_valid_authentication(
    customer_context: tuple[TestClient, InMemoryCustomerRepository],
) -> None:
    client, repository = customer_context

    body = create_customer(client)

    assert body["name"] == "Ada Lovelace"
    assert body["email"] == "ada@example.com"
    assert body["phone"] == "+55 11 99999-0000"
    assert repository.get_by_id(body["id"]) is not None


@pytest.mark.parametrize(
    "payload",
    [
        {"name": "", "email": "ada@example.com"},
        {"name": "Ada", "email": "not-an-email"},
        {"name": "Ada", "email": "ada@example.com", "unknown": "value"},
    ],
)
def test_create_customer_rejects_invalid_payload(
    customer_context: tuple[TestClient, InMemoryCustomerRepository],
    payload: dict[str, str],
) -> None:
    client, _ = customer_context

    response = client.post("/api/v1/customers", json=payload)

    assert response.status_code == 422


def test_create_customer_rejects_duplicate_normalized_email(
    customer_context: tuple[TestClient, InMemoryCustomerRepository],
) -> None:
    client, _ = customer_context
    create_customer(client)

    response = client.post(
        "/api/v1/customers",
        json={"name": "Duplicate", "email": "ADA@example.com"},
    )

    assert response.status_code == 409


def test_list_customers_is_empty(
    customer_context: tuple[TestClient, InMemoryCustomerRepository],
) -> None:
    client, _ = customer_context

    response = client.get("/api/v1/customers")

    assert response.status_code == 200
    assert response.json() == {
        "items": [],
        "total": 0,
        "page": 1,
        "page_size": 20,
        "pages": 0,
    }


def test_list_customers_uses_deterministic_database_style_pagination(
    customer_context: tuple[TestClient, InMemoryCustomerRepository],
) -> None:
    client, _ = customer_context
    for index in range(1, 6):
        create_customer(
            client,
            name=f"Customer {index}",
            email=f"customer{index}@example.com",
            phone=None,
        )

    response = client.get("/api/v1/customers?page=2&page_size=2")

    assert response.status_code == 200
    body = response.json()
    assert [item["id"] for item in body["items"]] == [3, 4]
    assert body == {
        "items": body["items"],
        "total": 5,
        "page": 2,
        "page_size": 2,
        "pages": 3,
    }


@pytest.mark.parametrize("query", ["page=0", "page_size=0", "page_size=101"])
def test_list_customers_validates_pagination_limits(
    customer_context: tuple[TestClient, InMemoryCustomerRepository],
    query: str,
) -> None:
    client, _ = customer_context

    response = client.get(f"/api/v1/customers?{query}")

    assert response.status_code == 422


def test_get_existing_customer(
    customer_context: tuple[TestClient, InMemoryCustomerRepository],
) -> None:
    client, _ = customer_context
    created = create_customer(client)

    response = client.get(f"/api/v1/customers/{created['id']}")

    assert response.status_code == 200
    assert response.json() == created


def test_get_missing_customer_returns_404(
    customer_context: tuple[TestClient, InMemoryCustomerRepository],
) -> None:
    client, _ = customer_context

    response = client.get("/api/v1/customers/999")

    assert response.status_code == 404


def test_patch_customer_is_partial_and_preserves_omitted_fields(
    customer_context: tuple[TestClient, InMemoryCustomerRepository],
) -> None:
    client, _ = customer_context
    created = create_customer(client)

    response = client.patch(
        f"/api/v1/customers/{created['id']}",
        json={"name": "  Augusta Ada King  "},
    )

    assert response.status_code == 200
    assert response.json()["name"] == "Augusta Ada King"
    assert response.json()["email"] == created["email"]
    assert response.json()["phone"] == created["phone"]


def test_patch_customer_can_clear_optional_phone(
    customer_context: tuple[TestClient, InMemoryCustomerRepository],
) -> None:
    client, _ = customer_context
    created = create_customer(client)

    response = client.patch(
        f"/api/v1/customers/{created['id']}",
        json={"phone": None},
    )

    assert response.status_code == 200
    assert response.json()["phone"] is None


@pytest.mark.parametrize("payload", [{"name": None}, {"email": None}, {"id": 2}])
def test_patch_customer_accepts_only_valid_mutable_fields(
    customer_context: tuple[TestClient, InMemoryCustomerRepository],
    payload: dict[str, object],
) -> None:
    client, _ = customer_context
    created = create_customer(client)

    response = client.patch(
        f"/api/v1/customers/{created['id']}",
        json=payload,
    )

    assert response.status_code == 422


def test_patch_missing_customer_returns_404(
    customer_context: tuple[TestClient, InMemoryCustomerRepository],
) -> None:
    client, _ = customer_context

    response = client.patch("/api/v1/customers/999", json={"name": "Missing"})

    assert response.status_code == 404


def test_patch_customer_rejects_duplicate_email(
    customer_context: tuple[TestClient, InMemoryCustomerRepository],
) -> None:
    client, _ = customer_context
    first = create_customer(client)
    second = create_customer(
        client,
        name="Grace Hopper",
        email="grace@example.com",
        phone=None,
    )

    response = client.patch(
        f"/api/v1/customers/{second['id']}",
        json={"email": first["email"].upper()},
    )

    assert response.status_code == 409


def test_delete_customer_removes_record(
    customer_context: tuple[TestClient, InMemoryCustomerRepository],
) -> None:
    client, repository = customer_context
    created = create_customer(client)

    response = client.delete(f"/api/v1/customers/{created['id']}")

    assert response.status_code == 204
    assert response.content == b""
    assert repository.get_by_id(created["id"]) is None
    assert client.get(f"/api/v1/customers/{created['id']}").status_code == 404


def test_delete_missing_customer_returns_404(
    customer_context: tuple[TestClient, InMemoryCustomerRepository],
) -> None:
    client, _ = customer_context

    response = client.delete("/api/v1/customers/999")

    assert response.status_code == 404


def test_customer_openapi_documents_security_schemas_and_status_codes() -> None:
    schema = app.openapi()
    collection = schema["paths"]["/api/v1/customers"]
    resource = schema["paths"]["/api/v1/customers/{customer_id}"]

    assert set(collection) == {"get", "post"}
    assert set(resource) == {"get", "patch", "delete"}
    for operation in [*collection.values(), *resource.values()]:
        assert operation["security"] == [{"OAuth2PasswordBearer": []}]

    assert {"201", "401", "409", "422"} <= set(collection["post"]["responses"])
    assert {"200", "401", "404", "422"} <= set(resource["get"]["responses"])
    assert {"200", "401", "404", "409", "422"} <= set(resource["patch"]["responses"])
    assert {"204", "401", "404", "409", "422"} <= set(resource["delete"]["responses"])
    assert {
        "CustomerCreate",
        "CustomerListResponse",
        "CustomerResponse",
        "CustomerUpdate",
    } <= set(schema["components"]["schemas"])

from collections.abc import Generator
from datetime import UTC, datetime

import pytest
from fastapi.testclient import TestClient

from app.api.dependencies import (
    get_current_user,
    get_customer_service,
    get_user_service,
)
from app.main import app
from app.models.customer import Customer
from app.models.user import User, UserRole
from app.schemas.customer import CustomerListResponse


class CustomerServiceDouble:
    def list(self, *, page: int, page_size: int) -> CustomerListResponse:
        return CustomerListResponse(
            items=[], total=0, page=page, page_size=page_size, pages=0
        )

    def create(self, data: object) -> Customer:
        now = datetime.now(UTC)
        return Customer(
            id=1,
            name="Created",
            email="created@example.com",
            phone=None,
            created_at=now,
            updated_at=now,
        )


@pytest.fixture
def role_client() -> Generator[tuple[TestClient, User]]:
    now = datetime.now(UTC)
    user = User(
        id=1,
        full_name="Role Tester",
        email="role@example.com",
        hashed_password="not-used",
        role=UserRole.VIEWER,
        is_active=True,
        created_at=now,
        updated_at=now,
    )
    app.dependency_overrides[get_current_user] = lambda: user
    app.dependency_overrides[get_customer_service] = lambda: CustomerServiceDouble()
    with TestClient(app) as client:
        yield client, user
    app.dependency_overrides.clear()


def test_viewer_can_read_but_cannot_mutate(
    role_client: tuple[TestClient, User],
) -> None:
    client, user = role_client
    user.role = UserRole.VIEWER

    read = client.get("/api/v1/customers")
    mutation = client.post(
        "/api/v1/customers",
        json={"name": "Created", "email": "created@example.com"},
    )

    assert read.status_code == 200
    assert mutation.status_code == 403


def test_operator_can_mutate_but_cannot_administer_users(
    role_client: tuple[TestClient, User],
) -> None:
    client, user = role_client
    user.role = UserRole.OPERATOR

    mutation = client.post(
        "/api/v1/customers",
        json={"name": "Created", "email": "created@example.com"},
    )
    users = client.get("/api/v1/users")

    assert mutation.status_code == 201
    assert users.status_code == 403


def test_admin_can_access_administrative_route_dependency(
    role_client: tuple[TestClient, User],
) -> None:
    client, user = role_client
    user.role = UserRole.ADMIN

    class UserServiceDouble:
        def list(self, *, page: int, page_size: int) -> object:
            from app.schemas.user import UserListResponse

            return UserListResponse(
                items=[user], total=1, page=page, page_size=page_size, pages=1
            )

    app.dependency_overrides[get_user_service] = lambda: UserServiceDouble()
    response = client.get("/api/v1/users")

    assert response.status_code == 200


def test_missing_authentication_is_401_and_public_endpoints_remain_public() -> None:
    app.dependency_overrides.clear()
    with TestClient(app) as client:
        protected = client.get("/api/v1/customers")
        live = client.get("/health/live")
        stripe_webhook = client.post("/api/v1/webhooks/stripe", content=b"{}")

    assert protected.status_code == 401
    assert protected.headers["www-authenticate"] == "Bearer"
    assert live.status_code == 200
    assert stripe_webhook.status_code == 400

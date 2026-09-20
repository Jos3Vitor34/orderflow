from collections.abc import Generator
from datetime import UTC, datetime, timedelta

import jwt
import pytest
from fastapi.testclient import TestClient

from app.api.dependencies import get_auth_service
from app.core.config import Settings
from app.main import app
from app.models.user import User
from app.repositories.user import DuplicateEmailError
from app.services.auth import AuthService


class InMemoryUserRepository:
    def __init__(self) -> None:
        self.users: dict[int, User] = {}

    def get_by_id(self, user_id: int) -> User | None:
        return self.users.get(user_id)

    def get_by_email(self, email: str) -> User | None:
        return next(
            (user for user in self.users.values() if user.email.lower() == email),
            None,
        )

    def create(self, *, full_name: str, email: str, hashed_password: str) -> User:
        if self.get_by_email(email) is not None:
            raise DuplicateEmailError
        now = datetime.now(UTC)
        user = User(
            id=len(self.users) + 1,
            full_name=full_name,
            email=email,
            hashed_password=hashed_password,
            is_active=True,
            created_at=now,
            updated_at=now,
        )
        self.users[user.id] = user
        return user


@pytest.fixture
def auth_context() -> Generator[tuple[TestClient, InMemoryUserRepository, Settings]]:
    repository = InMemoryUserRepository()
    settings = Settings(
        jwt_secret_key="test-only-secret-key-that-is-at-least-32-characters",
        access_token_expire_minutes=30,
    )
    service = AuthService(repository, settings)  # type: ignore[arg-type]
    app.dependency_overrides[get_auth_service] = lambda: service

    with TestClient(app) as client:
        yield client, repository, settings

    app.dependency_overrides.clear()


def register_user(client: TestClient, email: str = "Admin@Example.COM") -> dict:
    response = client.post(
        "/api/v1/auth/register",
        json={
            "full_name": "  OrderFlow Admin  ",
            "email": email,
            "password": "strong-password",
        },
    )
    assert response.status_code == 201
    return response.json()


def login_user(client: TestClient, email: str = "admin@example.com") -> str:
    response = client.post(
        "/api/v1/auth/login",
        data={"username": email, "password": "strong-password"},
    )
    assert response.status_code == 200
    assert response.json()["token_type"] == "bearer"
    return response.json()["access_token"]


def test_register_normalizes_email_and_returns_only_public_data(
    auth_context: tuple[TestClient, InMemoryUserRepository, Settings],
) -> None:
    client, _, _ = auth_context

    body = register_user(client)

    assert body["full_name"] == "OrderFlow Admin"
    assert body["email"] == "admin@example.com"
    assert body["is_active"] is True
    assert "hashed_password" not in body
    assert "password" not in body


def test_register_rejects_duplicate_normalized_email(
    auth_context: tuple[TestClient, InMemoryUserRepository, Settings],
) -> None:
    client, _, _ = auth_context
    register_user(client)

    response = client.post(
        "/api/v1/auth/register",
        json={
            "full_name": "Duplicate",
            "email": "ADMIN@example.com",
            "password": "another-password",
        },
    )

    assert response.status_code == 409


def test_register_stores_argon2_hash_instead_of_plain_password(
    auth_context: tuple[TestClient, InMemoryUserRepository, Settings],
) -> None:
    client, repository, _ = auth_context
    register_user(client)

    saved_user = repository.get_by_email("admin@example.com")
    assert saved_user is not None
    assert saved_user.hashed_password != "strong-password"
    assert saved_user.hashed_password.startswith("$argon2")


def test_login_returns_oauth2_bearer_access_token(
    auth_context: tuple[TestClient, InMemoryUserRepository, Settings],
) -> None:
    client, _, settings = auth_context
    registered = register_user(client)

    token = login_user(client, " ADMIN@example.com ")
    payload = jwt.decode(
        token,
        settings.jwt_secret_key.get_secret_value(),
        algorithms=[settings.jwt_algorithm],
    )

    assert payload["sub"] == str(registered["id"])
    assert payload["type"] == "access"
    assert payload["exp"] - payload["iat"] == 30 * 60


@pytest.mark.parametrize(
    "payload",
    [
        {
            "full_name": "",
            "email": "admin@example.com",
            "password": "strong-password",
        },
        {
            "full_name": "Admin",
            "email": "not-an-email",
            "password": "strong-password",
        },
        {
            "full_name": "Admin",
            "email": "admin@example.com",
            "password": "short",
        },
    ],
)
def test_register_validates_input_with_pydantic(
    auth_context: tuple[TestClient, InMemoryUserRepository, Settings],
    payload: dict[str, str],
) -> None:
    client, _, _ = auth_context

    response = client.post("/api/v1/auth/register", json=payload)

    assert response.status_code == 422


@pytest.mark.parametrize(
    ("email", "password"),
    [
        ("admin@example.com", "wrong-password"),
        ("missing@example.com", "strong-password"),
    ],
)
def test_login_rejects_invalid_credentials(
    auth_context: tuple[TestClient, InMemoryUserRepository, Settings],
    email: str,
    password: str,
) -> None:
    client, _, _ = auth_context
    register_user(client)

    response = client.post(
        "/api/v1/auth/login",
        data={"username": email, "password": password},
    )

    assert response.status_code == 401
    assert response.headers["www-authenticate"] == "Bearer"


def test_me_returns_current_user_without_credentials(
    auth_context: tuple[TestClient, InMemoryUserRepository, Settings],
) -> None:
    client, _, _ = auth_context
    registered = register_user(client)
    token = login_user(client)

    response = client.get(
        "/api/v1/auth/me",
        headers={"Authorization": f"Bearer {token}"},
    )

    assert response.status_code == 200
    assert response.json() == registered
    assert "hashed_password" not in response.json()


def test_me_rejects_missing_token(
    auth_context: tuple[TestClient, InMemoryUserRepository, Settings],
) -> None:
    client, _, _ = auth_context

    response = client.get("/api/v1/auth/me")

    assert response.status_code == 401
    assert response.headers["www-authenticate"] == "Bearer"


def test_me_rejects_invalid_token(
    auth_context: tuple[TestClient, InMemoryUserRepository, Settings],
) -> None:
    client, _, settings = auth_context
    now = datetime.now(UTC)
    token = jwt.encode(
        {
            "sub": "1",
            "type": "access",
            "iat": now,
            "exp": now + timedelta(minutes=5),
        },
        "a-different-secret-key-with-at-least-32-characters",
        algorithm=settings.jwt_algorithm,
    )

    response = client.get(
        "/api/v1/auth/me",
        headers={"Authorization": f"Bearer {token}"},
    )

    assert response.status_code == 401
    assert response.headers["www-authenticate"] == "Bearer"


def test_me_rejects_expired_token(
    auth_context: tuple[TestClient, InMemoryUserRepository, Settings],
) -> None:
    client, _, settings = auth_context
    registered = register_user(client)
    now = datetime.now(UTC)
    token = jwt.encode(
        {
            "sub": str(registered["id"]),
            "type": "access",
            "iat": now - timedelta(minutes=2),
            "exp": now - timedelta(minutes=1),
        },
        settings.jwt_secret_key.get_secret_value(),
        algorithm=settings.jwt_algorithm,
    )

    response = client.get(
        "/api/v1/auth/me",
        headers={"Authorization": f"Bearer {token}"},
    )

    assert response.status_code == 401


def test_me_rejects_token_for_missing_user(
    auth_context: tuple[TestClient, InMemoryUserRepository, Settings],
) -> None:
    client, _, settings = auth_context
    now = datetime.now(UTC)
    token = jwt.encode(
        {
            "sub": "999",
            "type": "access",
            "iat": now,
            "exp": now + timedelta(minutes=5),
        },
        settings.jwt_secret_key.get_secret_value(),
        algorithm=settings.jwt_algorithm,
    )

    response = client.get(
        "/api/v1/auth/me",
        headers={"Authorization": f"Bearer {token}"},
    )

    assert response.status_code == 401


def test_inactive_user_cannot_login_or_use_existing_token(
    auth_context: tuple[TestClient, InMemoryUserRepository, Settings],
) -> None:
    client, repository, _ = auth_context
    register_user(client)
    token = login_user(client)
    user = repository.get_by_email("admin@example.com")
    assert user is not None
    user.is_active = False

    login_response = client.post(
        "/api/v1/auth/login",
        data={"username": "admin@example.com", "password": "strong-password"},
    )
    me_response = client.get(
        "/api/v1/auth/me",
        headers={"Authorization": f"Bearer {token}"},
    )

    assert login_response.status_code == 401
    assert me_response.status_code == 401

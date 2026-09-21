from collections.abc import Generator
from datetime import UTC, datetime, timedelta

import jwt
import pytest
from fastapi.testclient import TestClient

from app.api.dependencies import get_auth_service, get_user_service
from app.core.config import Settings
from app.main import app
from app.models.user import User, UserRole
from app.repositories.user import DuplicateEmailError
from app.schemas.user import UserCreate
from app.services.auth import AuthService, password_hash
from app.services.user import UserService


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

    def create(
        self,
        *,
        full_name: str,
        email: str,
        hashed_password: str,
        role: UserRole,
    ) -> User:
        if self.get_by_email(email) is not None:
            raise DuplicateEmailError
        now = datetime.now(UTC)
        user = User(
            id=len(self.users) + 1,
            full_name=full_name,
            email=email,
            hashed_password=hashed_password,
            role=role,
            is_active=True,
            created_at=now,
            updated_at=now,
        )
        self.users[user.id] = user
        return user

    def list_page(self, *, offset: int, limit: int) -> tuple[list[User], int]:
        users = list(self.users.values())
        return users[offset : offset + limit], len(users)

    def lock_active_admins(self) -> list[User]:
        return [
            user
            for user in self.users.values()
            if user.role == UserRole.ADMIN and user.is_active
        ]

    def update(self, user: User, changes: dict[str, object]) -> User:
        for field, value in changes.items():
            setattr(user, field, value)
        user.updated_at = datetime.now(UTC)
        return user


@pytest.fixture
def auth_context() -> Generator[
    tuple[TestClient, InMemoryUserRepository, Settings, UserService]
]:
    repository = InMemoryUserRepository()
    settings = Settings(
        jwt_secret_key="test-only-secret-key-that-is-at-least-32-characters",
        access_token_expire_minutes=30,
    )
    auth_service = AuthService(repository, settings)  # type: ignore[arg-type]
    user_service = UserService(repository)  # type: ignore[arg-type]
    user_service.create(
        UserCreate(
            full_name="OrderFlow Admin",
            email="admin@example.com",
            password="strong-password",
            role=UserRole.ADMIN,
        )
    )
    app.dependency_overrides[get_auth_service] = lambda: auth_service
    app.dependency_overrides[get_user_service] = lambda: user_service

    with TestClient(app) as client:
        yield client, repository, settings, user_service

    app.dependency_overrides.clear()


def login_user(
    client: TestClient,
    email: str = "admin@example.com",
    password: str = "strong-password",
) -> str:
    response = client.post(
        "/api/v1/auth/login",
        data={"username": email, "password": password},
    )
    assert response.status_code == 200
    assert response.json()["token_type"] == "bearer"
    return response.json()["access_token"]


def test_public_registration_is_not_available(
    auth_context: tuple[TestClient, InMemoryUserRepository, Settings, UserService],
) -> None:
    client, _, _, _ = auth_context
    response = client.post(
        "/api/v1/auth/register",
        json={
            "full_name": "Public User",
            "email": "public@example.com",
            "password": "strong-password",
        },
    )
    assert response.status_code == 404


def test_admin_creates_user_with_argon2_hash_and_safe_response(
    auth_context: tuple[TestClient, InMemoryUserRepository, Settings, UserService],
) -> None:
    client, repository, _, _ = auth_context
    token = login_user(client)
    response = client.post(
        "/api/v1/users",
        headers={"Authorization": f"Bearer {token}"},
        json={
            "full_name": "  Read Only  ",
            "email": "Viewer@Example.com",
            "password": "viewer-password",
            "role": "viewer",
        },
    )
    assert response.status_code == 201
    body = response.json()
    assert body["full_name"] == "Read Only"
    assert body["email"] == "viewer@example.com"
    assert body["role"] == "viewer"
    assert "password" not in body
    assert "hashed_password" not in body
    saved = repository.get_by_email("viewer@example.com")
    assert saved is not None
    assert saved.hashed_password.startswith("$argon2")
    assert saved.hashed_password != "viewer-password"
    assert password_hash.verify("viewer-password", saved.hashed_password)


def test_admin_user_creation_rejects_duplicate_and_invalid_password(
    auth_context: tuple[TestClient, InMemoryUserRepository, Settings, UserService],
) -> None:
    client, _, _, _ = auth_context
    headers = {"Authorization": f"Bearer {login_user(client)}"}
    duplicate = client.post(
        "/api/v1/users",
        headers=headers,
        json={
            "full_name": "Duplicate",
            "email": "ADMIN@example.com",
            "password": "another-password",
        },
    )
    invalid = client.post(
        "/api/v1/users",
        headers=headers,
        json={
            "full_name": "Short Password",
            "email": "short@example.com",
            "password": "short",
        },
    )
    assert duplicate.status_code == 409
    assert invalid.status_code == 422


def test_login_returns_role_bearing_oauth2_access_token(
    auth_context: tuple[TestClient, InMemoryUserRepository, Settings, UserService],
) -> None:
    client, repository, settings, _ = auth_context
    admin = repository.get_by_email("admin@example.com")
    assert admin is not None
    token = login_user(client, " ADMIN@example.com ")
    payload = jwt.decode(
        token,
        settings.jwt_secret_key.get_secret_value(),
        algorithms=[settings.jwt_algorithm],
    )
    assert payload["sub"] == str(admin.id)
    assert payload["role"] == "admin"
    assert payload["type"] == "access"
    assert payload["exp"] - payload["iat"] == 30 * 60


@pytest.mark.parametrize(
    ("email", "password"),
    [
        ("admin@example.com", "wrong-password"),
        ("missing@example.com", "strong-password"),
    ],
)
def test_login_rejects_invalid_credentials_without_account_disclosure(
    auth_context: tuple[TestClient, InMemoryUserRepository, Settings, UserService],
    email: str,
    password: str,
) -> None:
    client, _, _, _ = auth_context
    response = client.post(
        "/api/v1/auth/login",
        data={"username": email, "password": password},
    )
    assert response.status_code == 401
    assert response.headers["www-authenticate"] == "Bearer"
    assert response.json()["detail"] == "Incorrect e-mail or password"


def test_me_returns_current_user_without_credentials(
    auth_context: tuple[TestClient, InMemoryUserRepository, Settings, UserService],
) -> None:
    client, _, _, _ = auth_context
    response = client.get(
        "/api/v1/auth/me",
        headers={"Authorization": f"Bearer {login_user(client)}"},
    )
    assert response.status_code == 200
    assert response.json()["role"] == "admin"
    assert "hashed_password" not in response.json()


def test_me_rejects_missing_and_malformed_tokens(
    auth_context: tuple[TestClient, InMemoryUserRepository, Settings, UserService],
) -> None:
    client, _, _, _ = auth_context
    missing = client.get("/api/v1/auth/me")
    malformed = client.get(
        "/api/v1/auth/me",
        headers={"Authorization": "Bearer not-a-jwt"},
    )
    assert missing.status_code == 401
    assert malformed.status_code == 401
    assert missing.headers["www-authenticate"] == "Bearer"
    assert malformed.headers["www-authenticate"] == "Bearer"


@pytest.mark.parametrize("failure", ["expired", "signature", "missing_user"])
def test_me_rejects_invalid_token_variants(
    auth_context: tuple[TestClient, InMemoryUserRepository, Settings, UserService],
    failure: str,
) -> None:
    client, repository, settings, _ = auth_context
    admin = repository.get_by_email("admin@example.com")
    assert admin is not None
    now = datetime.now(UTC)
    payload = {
        "sub": "999" if failure == "missing_user" else str(admin.id),
        "role": "admin",
        "type": "access",
        "iat": now - timedelta(minutes=2) if failure == "expired" else now,
        "exp": now - timedelta(minutes=1)
        if failure == "expired"
        else now + timedelta(minutes=5),
    }
    secret = (
        "a-different-secret-key-with-at-least-32-characters"
        if failure == "signature"
        else settings.jwt_secret_key.get_secret_value()
    )
    token = jwt.encode(payload, secret, algorithm=settings.jwt_algorithm)
    response = client.get(
        "/api/v1/auth/me",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert response.status_code == 401


def test_inactive_user_cannot_login_or_use_existing_token(
    auth_context: tuple[TestClient, InMemoryUserRepository, Settings, UserService],
) -> None:
    client, repository, _, _ = auth_context
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


def test_role_change_invalidates_existing_token(
    auth_context: tuple[TestClient, InMemoryUserRepository, Settings, UserService],
) -> None:
    client, repository, _, _ = auth_context
    token = login_user(client)
    user = repository.get_by_email("admin@example.com")
    assert user is not None
    user.role = UserRole.OPERATOR
    response = client.get(
        "/api/v1/auth/me",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert response.status_code == 401


def test_last_active_admin_cannot_be_demoted_or_deactivated(
    auth_context: tuple[TestClient, InMemoryUserRepository, Settings, UserService],
) -> None:
    client, repository, _, _ = auth_context
    admin = repository.get_by_email("admin@example.com")
    assert admin is not None
    headers = {"Authorization": f"Bearer {login_user(client)}"}
    demote = client.patch(
        f"/api/v1/users/{admin.id}/role",
        headers=headers,
        json={"role": "operator"},
    )
    deactivate = client.patch(
        f"/api/v1/users/{admin.id}/active",
        headers=headers,
        json={"is_active": False},
    )
    assert demote.status_code == 409
    assert deactivate.status_code == 409
    assert admin.role == UserRole.ADMIN
    assert admin.is_active is True


def test_admin_can_change_role_and_active_state_of_another_user(
    auth_context: tuple[TestClient, InMemoryUserRepository, Settings, UserService],
) -> None:
    client, _, _, user_service = auth_context
    viewer = user_service.create(
        UserCreate(
            full_name="Managed Viewer",
            email="managed@example.com",
            password="managed-password",
            role=UserRole.VIEWER,
        )
    )
    headers = {"Authorization": f"Bearer {login_user(client)}"}

    role_response = client.patch(
        f"/api/v1/users/{viewer.id}/role",
        headers=headers,
        json={"role": "operator"},
    )
    active_response = client.patch(
        f"/api/v1/users/{viewer.id}/active",
        headers=headers,
        json={"is_active": False},
    )

    assert role_response.status_code == 200
    assert role_response.json()["role"] == "operator"
    assert active_response.status_code == 200
    assert active_response.json()["is_active"] is False


def test_viewer_cannot_administer_users(
    auth_context: tuple[TestClient, InMemoryUserRepository, Settings, UserService],
) -> None:
    client, _, _, user_service = auth_context
    user_service.create(
        UserCreate(
            full_name="Viewer",
            email="viewer@example.com",
            password="viewer-password",
            role=UserRole.VIEWER,
        )
    )
    token = login_user(client, "viewer@example.com", "viewer-password")
    response = client.get(
        "/api/v1/users",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert response.status_code == 403


def test_user_list_and_detail_never_expose_hash(
    auth_context: tuple[TestClient, InMemoryUserRepository, Settings, UserService],
) -> None:
    client, repository, _, _ = auth_context
    admin = repository.get_by_email("admin@example.com")
    assert admin is not None
    headers = {"Authorization": f"Bearer {login_user(client)}"}
    listed = client.get("/api/v1/users", headers=headers)
    detail = client.get(f"/api/v1/users/{admin.id}", headers=headers)
    assert listed.status_code == 200
    assert detail.status_code == 200
    assert "hashed_password" not in listed.text
    assert "hashed_password" not in detail.text

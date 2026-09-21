from collections.abc import Iterator

import pytest
from pydantic import ValidationError

from app import cli
from app.core.config import Settings
from app.models.user import UserRole
from app.schemas.user import UserCreate


class SessionContextDouble:
    def __enter__(self) -> object:
        return object()

    def __exit__(self, *_: object) -> None:
        return None


def test_bootstrap_admin_prompts_twice_without_echo_and_forces_admin_role(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: list[UserCreate] = []
    passwords: Iterator[str] = iter(["bootstrap-password", "bootstrap-password"])

    class UserServiceDouble:
        def __init__(self, repository: object) -> None:
            self.repository = repository

        def create(self, data: UserCreate) -> object:
            captured.append(data)
            return object()

    monkeypatch.delenv("ORDERFLOW_ADMIN_PASSWORD", raising=False)
    monkeypatch.setattr(cli.getpass, "getpass", lambda prompt: next(passwords))
    monkeypatch.setattr(cli, "SessionLocal", lambda: SessionContextDouble())
    monkeypatch.setattr(cli, "UserService", UserServiceDouble)

    result = cli.bootstrap_admin(
        email="ADMIN@example.com",
        full_name="Bootstrap Admin",
    )

    assert result == 0
    assert captured[0].role == UserRole.ADMIN
    assert captured[0].password == "bootstrap-password"


def test_production_rejects_the_compose_only_jwt_secret() -> None:
    with pytest.raises(ValidationError, match="JWT_SECRET_KEY must be replaced"):
        Settings(
            environment="production",
            jwt_secret_key="local-docker-only-secret-change-before-use",
        )

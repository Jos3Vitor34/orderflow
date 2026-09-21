import pytest
from fastapi.testclient import TestClient
from redis.exceptions import TimeoutError as RedisTimeoutError
from sqlalchemy.exc import OperationalError

from app.api.dependencies import get_readiness_service
from app.core.config import Settings
from app.main import app
from app.services.health import ReadinessService


class SessionDouble:
    def __init__(self, failure: Exception | None = None) -> None:
        self.failure = failure
        self.closed = False

    def __enter__(self) -> "SessionDouble":
        return self

    def __exit__(self, *_: object) -> None:
        self.closed = True

    def execute(self, statement: object) -> None:
        if self.failure:
            raise self.failure


class RedisDouble:
    def __init__(self, failure: Exception | None = None) -> None:
        self.failure = failure
        self.closed = False

    def ping(self) -> bool:
        if self.failure:
            raise self.failure
        return True

    def close(self) -> None:
        self.closed = True


def build_service(
    *,
    postgres_failure: Exception | None = None,
    redis_failure: Exception | None = None,
) -> tuple[ReadinessService, SessionDouble, RedisDouble, dict[str, object]]:
    session = SessionDouble(postgres_failure)
    redis = RedisDouble(redis_failure)
    redis_options: dict[str, object] = {}

    def redis_factory(url: str, **options: object) -> RedisDouble:
        redis_options["url"] = url
        redis_options.update(options)
        return redis

    settings = Settings(
        jwt_secret_key="test-only-secret-key-that-is-at-least-32-characters",
        readiness_timeout_seconds=0.25,
        redis_url="redis://example.invalid:6379/0",
    )
    service = ReadinessService(
        lambda: session,  # type: ignore[arg-type]
        settings,
        redis_factory,  # type: ignore[arg-type]
    )
    return service, session, redis, redis_options


def test_liveness_is_public_and_does_not_resolve_readiness_dependencies() -> None:
    calls = 0

    def forbidden_dependency() -> object:
        nonlocal calls
        calls += 1
        raise AssertionError("liveness must not check dependencies")

    app.dependency_overrides[get_readiness_service] = forbidden_dependency
    try:
        with TestClient(app) as client:
            response = client.get("/health/live")
            alias = client.get("/health")
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 200
    assert alias.json() == {"status": "ok"}
    assert calls == 0


@pytest.mark.parametrize(
    ("postgres_failure", "redis_failure", "component"),
    [
        (OperationalError("SELECT", {}, OSError("down")), None, "postgres"),
        (None, RedisTimeoutError("timed out"), "redis"),
    ],
)
def test_readiness_reports_dependency_failure_closes_resources_and_hides_details(
    postgres_failure: Exception | None,
    redis_failure: Exception | None,
    component: str,
) -> None:
    service, session, redis, options = build_service(
        postgres_failure=postgres_failure,
        redis_failure=redis_failure,
    )
    app.dependency_overrides[get_readiness_service] = lambda: service
    try:
        with TestClient(app) as client:
            response = client.get("/health/ready")
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 503
    assert response.json()["status"] == "not_ready"
    assert response.json()["components"][component] == "unavailable"
    assert "example.invalid" not in response.text
    assert "timed out" not in response.text
    assert session.closed is True
    assert redis.closed is True
    assert options["socket_connect_timeout"] == 0.25
    assert options["socket_timeout"] == 0.25


def test_readiness_healthy_returns_200() -> None:
    service, session, redis, _ = build_service()
    app.dependency_overrides[get_readiness_service] = lambda: service
    try:
        with TestClient(app) as client:
            response = client.get("/health/ready")
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 200
    assert response.json() == {
        "status": "ready",
        "components": {"postgres": "ok", "redis": "ok"},
    }
    assert session.closed is True
    assert redis.closed is True

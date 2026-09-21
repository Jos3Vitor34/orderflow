from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy.exc import OperationalError

from app.celery_app import celery_app
from app.core.context import correlation_id_var, get_correlation_id
from app.core.middleware import CorrelationIdMiddleware
from app.tasks.base import ResilientDatabaseTask

observed_retries: list[str | None] = []


@celery_app.task(
    bind=True,
    base=ResilientDatabaseTask,
    name="orderflow.tests.correlation_probe",
)
def correlation_probe(self: ResilientDatabaseTask, entity_id: int) -> dict[str, Any]:
    return {
        "entity_id": entity_id,
        "correlation_id": get_correlation_id(),
        "task_id": self.request.id,
        "headers": dict(self.request.headers or {}),
    }


@celery_app.task(
    bind=True,
    base=ResilientDatabaseTask,
    name="orderflow.tests.retry_correlation_probe",
)
def retry_correlation_probe(self: ResilientDatabaseTask) -> str | None:
    observed_retries.append(get_correlation_id())
    if self.request.retries == 0:
        raise OperationalError("SELECT", {}, OSError("temporary"))
    return get_correlation_id()


def test_api_context_is_published_in_headers_and_read_by_eager_worker() -> None:
    test_app = FastAPI()
    test_app.add_middleware(CorrelationIdMiddleware)

    @test_app.post("/publish")
    def publish() -> dict[str, Any]:
        return correlation_probe.delay(42).get()

    with TestClient(test_app) as client:
        response = client.post(
            "/publish",
            headers={"X-Correlation-ID": "api-to-worker-123"},
        )

    assert response.status_code == 200
    assert response.json()["entity_id"] == 42
    assert response.json()["correlation_id"] == "api-to-worker-123"
    assert response.json()["headers"]["x-correlation-id"] == "api-to-worker-123"
    assert response.json()["task_id"]
    assert correlation_id_var.get() is None


def test_task_outside_http_gets_own_id_and_context_is_cleaned() -> None:
    first = correlation_probe.delay(1).get()
    second = correlation_probe.delay(2).get()

    assert first["correlation_id"]
    assert second["correlation_id"]
    assert first["correlation_id"] != second["correlation_id"]
    assert correlation_id_var.get() is None


def test_retry_preserves_correlation_id_and_public_arguments(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    observed_retries.clear()
    monkeypatch.setitem(celery_app.conf, "task_eager_propagates", False)
    result = retry_correlation_probe.apply_async(
        headers={"x-correlation-id": "retry-123"}
    ).get(propagate=True)

    assert result == "retry-123"
    assert observed_retries == ["retry-123", "retry-123"]
    assert retry_correlation_probe.signature().args == ()
    assert correlation_id_var.get() is None

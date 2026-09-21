from concurrent.futures import ThreadPoolExecutor

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.core.context import correlation_id_var
from app.core.middleware import CorrelationIdMiddleware
from app.main import app


def test_valid_correlation_id_is_preserved_and_absent_id_is_generated() -> None:
    with TestClient(app) as client:
        preserved = client.get(
            "/health/live",
            headers={"X-Correlation-ID": "client.request-123:abc"},
        )
        generated = client.get("/health/live")

    assert preserved.headers["x-correlation-id"] == "client.request-123:abc"
    assert generated.headers["x-correlation-id"]
    assert generated.headers["x-correlation-id"] != "client.request-123:abc"
    assert correlation_id_var.get() is None


def test_invalid_correlation_ids_are_silently_replaced() -> None:
    with TestClient(app) as client:
        newline = client.get(
            "/health/live",
            headers={"X-Correlation-ID": "bad id with spaces"},
        )
        oversized = client.get(
            "/health/live",
            headers={"X-Correlation-ID": "x" * 129},
        )

    assert newline.status_code == 200
    assert newline.headers["x-correlation-id"] != "bad id with spaces"
    assert oversized.headers["x-correlation-id"] != "x" * 129


def test_error_response_contains_correlation_id_and_no_traceback() -> None:
    test_app = FastAPI()
    test_app.add_middleware(CorrelationIdMiddleware)

    @test_app.get("/failure")
    def failure() -> None:
        raise RuntimeError("internal details")

    with TestClient(test_app, raise_server_exceptions=False) as client:
        response = client.get(
            "/failure",
            headers={"X-Correlation-ID": "failure-123"},
        )

    assert response.status_code == 500
    assert response.headers["x-correlation-id"] == "failure-123"
    assert response.json() == {"detail": "Internal server error"}
    assert "RuntimeError" not in response.text


def test_concurrent_requests_keep_independent_contexts() -> None:
    def request(value: str) -> str:
        with TestClient(app) as client:
            return client.get(
                "/health/live",
                headers={"X-Correlation-ID": value},
            ).headers["x-correlation-id"]

    values = [f"parallel-{index}" for index in range(8)]
    with ThreadPoolExecutor(max_workers=4) as executor:
        results = list(executor.map(request, values))

    assert results == values
    assert correlation_id_var.get() is None

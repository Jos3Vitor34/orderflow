from fastapi.testclient import TestClient

from app.api.router import api_router
from app.main import app

client = TestClient(app)


def test_health_check() -> None:
    response = client.get("/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_api_router_uses_v1_prefix() -> None:
    assert api_router.prefix == "/api/v1"

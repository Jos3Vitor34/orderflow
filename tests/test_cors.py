import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from app.core.config import Settings
from app.main import app, settings

ALLOWED_ORIGIN = settings.cors_origin_list[0]


def test_cors_origins_are_parsed_from_environment(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(
        "CORS_ORIGINS",
        " http://localhost:5173, https://panel.example.com ,, ",
    )
    configured = Settings(_env_file=None)

    assert configured.cors_origin_list == [
        "http://localhost:5173",
        "https://panel.example.com",
    ]


@pytest.mark.parametrize(
    "origins",
    [
        "",
        " , ",
        "*",
        "http://localhost:5173,*",
        "ftp://panel.example.com",
        "https://panel.example.com/path",
        "https://panel.example.com?query=value",
        "https://panel.example.com#fragment",
        "https://user:password@panel.example.com",
    ],
)
def test_cors_rejects_wildcard_and_non_origin_values(origins: str) -> None:
    with pytest.raises(ValidationError, match="CORS_ORIGINS"):
        Settings(cors_origins=origins, _env_file=None)


def test_configured_frontend_preflight_allows_api_headers() -> None:
    with TestClient(app) as client:
        response = client.options(
            "/api/v1/payments/stripe",
            headers={
                "Origin": ALLOWED_ORIGIN,
                "Access-Control-Request-Method": "POST",
                "Access-Control-Request-Headers": "authorization,content-type,idempotency-key",
            },
        )

    assert response.status_code == 200
    assert response.headers["access-control-allow-origin"] == ALLOWED_ORIGIN
    allowed_methods = response.headers["access-control-allow-methods"]
    for method in ("GET", "POST", "PATCH", "DELETE"):
        assert method in allowed_methods
    assert "access-control-allow-credentials" not in response.headers
    allowed_headers = response.headers["access-control-allow-headers"].lower()
    assert "authorization" in allowed_headers
    assert "idempotency-key" in allowed_headers


def test_unknown_origin_is_not_allowed() -> None:
    with TestClient(app) as client:
        response = client.options(
            "/api/v1/payments/stripe",
            headers={
                "Origin": "https://untrusted.example",
                "Access-Control-Request-Method": "POST",
            },
        )

    assert response.status_code == 400
    assert "access-control-allow-origin" not in response.headers


def test_authentication_error_exposes_cors_header_to_frontend() -> None:
    with TestClient(app) as client:
        response = client.get("/api/v1/auth/me", headers={"Origin": ALLOWED_ORIGIN})

    assert response.status_code == 401
    assert response.headers["access-control-allow-origin"] == ALLOWED_ORIGIN

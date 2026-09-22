"""Guard the public API boundary documented to clients."""

import json

from app.main import app


def test_openapi_security_boundary_and_sensitive_schemas() -> None:
    schema = app.openapi()
    assert schema["openapi"].startswith("3.1.")
    assert schema["info"]["title"] == "OrderFlow API"
    assert len(schema["paths"]) == 25
    assert "OAuth2PasswordBearer" in schema["components"]["securitySchemes"]

    public = {
        ("post", "/api/v1/auth/login"),
        ("post", "/api/v1/webhooks/stripe"),
        ("get", "/health"),
        ("get", "/health/live"),
        ("get", "/health/ready"),
    }
    operations = {
        (method, path): operation
        for path, methods in schema["paths"].items()
        for method, operation in methods.items()
    }
    assert len(operations) == 37
    assert {
        key for key, operation in operations.items() if "security" not in operation
    } == public
    for key, operation in operations.items():
        if key not in public:
            assert operation["security"] == [{"OAuth2PasswordBearer": []}]

    serialized = json.dumps(schema).lower()
    assert "hashed_password" not in serialized
    assert "jwt_secret_key" not in serialized
    assert "stripe_secret_key" not in serialized
    assert "stripe_webhook_secret" not in serialized
    assert "503" in serialized
    assert "422" in serialized
    assert "403" in serialized
    assert "401" in serialized

from pathlib import Path
from secrets import token_hex
from unittest.mock import patch

from app.core.config import Settings
from app.server import main


def test_settings_load_mounted_secrets_without_dotenv(
    tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.delenv("JWT_SECRET_KEY", raising=False)
    monkeypatch.delenv("DATABASE_URL", raising=False)
    secret = token_hex(32)
    (tmp_path / "JWT_SECRET_KEY").write_text(secret)
    (tmp_path / "DATABASE_URL").write_text(
        "postgresql+psycopg://orderflow:synthetic@postgres:5432/orderflow"
    )
    settings = Settings(_env_file=None, _secrets_dir=tmp_path)
    assert settings.jwt_secret_key.get_secret_value() == secret
    assert settings.database_url.hosts()[0]["host"] == "postgres"
    assert secret not in repr(settings.jwt_secret_key)


def test_environment_retains_priority_over_mounted_secrets(
    tmp_path: Path, monkeypatch
) -> None:
    (tmp_path / "JWT_SECRET_KEY").write_text(token_hex(32))
    environment_secret = token_hex(32)
    monkeypatch.setenv("JWT_SECRET_KEY", environment_secret)
    settings = Settings(_env_file=None, _secrets_dir=tmp_path)
    assert settings.jwt_secret_key.get_secret_value() == environment_secret


def test_server_does_not_trust_arbitrary_forwarded_headers() -> None:
    with patch("app.server.uvicorn.run") as run:
        main()
    assert run.call_args.kwargs["proxy_headers"] is False

from functools import lru_cache
from pathlib import Path
from typing import Literal
from urllib.parse import urlsplit

from pydantic import (
    Field,
    PostgresDsn,
    RedisDsn,
    SecretStr,
    field_validator,
    model_validator,
)
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        secrets_dir="/run/secrets" if Path("/run/secrets").is_dir() else None,
    )

    database_url: PostgresDsn = PostgresDsn(
        "postgresql+psycopg://orderflow:orderflow_password@localhost:5432/orderflow"
    )
    redis_url: RedisDsn = RedisDsn("redis://localhost:6379/0")
    celery_broker_url: str = "redis://localhost:6379/0"
    celery_result_backend: str = "redis://localhost:6379/1"
    celery_task_always_eager: bool = False
    celery_task_eager_propagates: bool = False
    jwt_secret_key: SecretStr = Field(min_length=32)
    jwt_algorithm: Literal["HS256", "HS384", "HS512"] = "HS256"
    access_token_expire_minutes: int = Field(default=30, gt=0)
    orderflow_admin_password: SecretStr | None = None
    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"] = "INFO"
    log_format: Literal["json", "text"] = "json"
    service_name: str = Field(default="orderflow-api", min_length=1, max_length=64)
    environment: str = Field(default="development", min_length=1, max_length=32)
    correlation_id_max_length: int = Field(default=128, ge=16, le=256)
    readiness_timeout_seconds: float = Field(default=2.0, gt=0, le=10)
    cors_origins: str = "http://localhost:5173,http://127.0.0.1:5173"
    stripe_secret_key: SecretStr | None = None
    stripe_webhook_secret: SecretStr | None = None
    stripe_currency: Literal["brl"] = "brl"

    @field_validator("cors_origins")
    @classmethod
    def validate_cors_origins(cls, value: str) -> str:
        origins = [origin.strip() for origin in value.split(",") if origin.strip()]
        if not origins:
            raise ValueError("CORS_ORIGINS must contain at least one origin")
        for origin in origins:
            parsed = urlsplit(origin)
            if (
                parsed.scheme not in {"http", "https"}
                or not parsed.netloc
                or parsed.path
                or parsed.query
                or parsed.fragment
                or parsed.username
                or parsed.password
            ):
                raise ValueError("CORS_ORIGINS must contain HTTP origins only")
        return ",".join(origins)

    @property
    def cors_origin_list(self) -> list[str]:
        return self.cors_origins.split(",")

    @model_validator(mode="after")
    def reject_insecure_production_jwt_secret(self) -> "Settings":
        if (
            self.environment.lower() in {"prod", "production"}
            and self.jwt_secret_key.get_secret_value()
            == "local-docker-only-secret-change-before-use"
        ):
            raise ValueError("JWT_SECRET_KEY must be replaced in production")
        return self


@lru_cache
def get_settings() -> Settings:
    # BaseSettings obtains the required secret from the environment at runtime.
    return Settings()  # type: ignore[call-arg]

import logging
from collections.abc import Callable

from redis import Redis
from redis.exceptions import RedisError
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.core.config import Settings
from app.schemas.health import ReadinessResponse

logger = logging.getLogger(__name__)


class ReadinessService:
    def __init__(
        self,
        session_factory: Callable[[], Session],
        settings: Settings,
        redis_factory: Callable[..., Redis] = Redis.from_url,
    ) -> None:
        self._session_factory = session_factory
        self._settings = settings
        self._redis_factory = redis_factory

    def check(self) -> ReadinessResponse:
        components = {
            "postgres": self._check_postgres(),
            "redis": self._check_redis(),
        }
        return ReadinessResponse(
            status=(
                "ready"
                if all(state == "ok" for state in components.values())
                else "not_ready"
            ),
            components=components,
        )

    def _check_postgres(self) -> str:
        try:
            with self._session_factory() as session:
                session.execute(text("SELECT 1"))
        except SQLAlchemyError as exc:
            logger.warning(
                "Readiness PostgreSQL check failed",
                extra={"exception_type": type(exc).__name__, "component": "postgres"},
            )
            return "unavailable"
        return "ok"

    def _check_redis(self) -> str:
        timeout = self._settings.readiness_timeout_seconds
        client: Redis | None = None
        result = "ok"
        try:
            client = self._redis_factory(
                str(self._settings.redis_url),
                socket_connect_timeout=timeout,
                socket_timeout=timeout,
            )
            client.ping()
        except (RedisError, OSError, ValueError) as exc:
            logger.warning(
                "Readiness Redis check failed",
                extra={"exception_type": type(exc).__name__, "component": "redis"},
            )
            result = "unavailable"
        finally:
            if client is not None:
                try:
                    client.close()
                except (RedisError, OSError) as exc:
                    logger.warning(
                        "Readiness Redis cleanup failed",
                        extra={
                            "exception_type": type(exc).__name__,
                            "component": "redis",
                        },
                    )
                    result = "unavailable"
        return result

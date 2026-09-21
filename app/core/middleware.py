import logging
from time import perf_counter

from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from app.core.context import correlation_id_or_new, correlation_id_var

logger = logging.getLogger("orderflow.http")


class CorrelationIdMiddleware:
    def __init__(self, app: ASGIApp, *, max_length: int = 128) -> None:
        self.app = app
        self.max_length = max_length

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        incoming = next(
            (
                value.decode("latin-1")
                for key, value in scope.get("headers", [])
                if key.lower() == b"x-correlation-id"
            ),
            None,
        )
        correlation_id = correlation_id_or_new(
            incoming,
            max_length=self.max_length,
        )
        token = correlation_id_var.set(correlation_id)
        started_at = perf_counter()
        status_code = 500
        response_started = False

        async def send_with_correlation(message: Message) -> None:
            nonlocal response_started, status_code
            if message["type"] == "http.response.start":
                response_started = True
                status_code = message["status"]
                headers = list(message.get("headers", []))
                headers.append((b"x-correlation-id", correlation_id.encode("ascii")))
                message["headers"] = headers
            await send(message)

        try:
            try:
                await self.app(scope, receive, send_with_correlation)
            except Exception:
                logger.exception("Unhandled HTTP request error")
                if response_started:
                    raise
                response = JSONResponse(
                    status_code=500,
                    content={"detail": "Internal server error"},
                )
                await response(scope, receive, send_with_correlation)
        finally:
            logger.info(
                "HTTP request completed",
                extra={
                    "http_method": scope["method"],
                    "http_path": scope["path"],
                    "http_status": status_code,
                    "duration_ms": round((perf_counter() - started_at) * 1000, 3),
                },
            )
            correlation_id_var.reset(token)

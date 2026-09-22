import json
import logging
import re
from datetime import UTC, datetime
from typing import Any

from app.core.config import Settings
from app.core.context import (
    correlation_id_var,
    retry_attempt_var,
    task_id_var,
    task_name_var,
)

_STANDARD_RECORD_ATTRIBUTES = frozenset(
    logging.LogRecord("", 0, "", 0, "", (), None).__dict__
) | {"message", "asctime"}
_SENSITIVE_KEY = re.compile(
    r"(^|[_-])(authorization|cookie|password|passwd|hashed_password|jwt|secret|"
    r"signature|api[_-]?key|access[_-]?token|refresh[_-]?token)($|[_-])",
    re.IGNORECASE,
)
_TEXT_REDACTIONS = (
    (re.compile(r"(?i)Bearer\s+[A-Za-z0-9._~+/=-]+"), "Bearer [REDACTED]"),
    (
        re.compile(r"\beyJ[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\b"),
        "[REDACTED]",
    ),
    (re.compile(r"\b(?:sk|pk)_(?:test|live)_[A-Za-z0-9]+\b"), "[REDACTED]"),
    (re.compile(r"\bwhsec_[A-Za-z0-9]+\b"), "[REDACTED]"),
    (
        re.compile(r"(?i)(password|passwd|secret|api[_-]?key)=([^\s&,]+)"),
        r"\1=[REDACTED]",
    ),
    (re.compile(r"([a-z][a-z0-9+.-]*://[^:/\s]+:)[^@/\s]+(@)"), r"\1[REDACTED]\2"),
)


def sanitize_text(value: object) -> str:
    text = str(value)
    for pattern, replacement in _TEXT_REDACTIONS:
        text = pattern.sub(replacement, text)
    return text


def sanitize_value(key: str, value: Any) -> Any:
    if _SENSITIVE_KEY.search(key):
        return "[REDACTED]"
    if isinstance(value, dict):
        return {str(k): sanitize_value(str(k), v) for k, v in value.items()}
    if isinstance(value, (list, tuple, set, frozenset)):
        return [sanitize_value(key, item) for item in value]
    if isinstance(value, (str, bytes)):
        return sanitize_text(value)
    if value is None or isinstance(value, (bool, int, float)):
        return value
    return sanitize_text(value)


class JsonFormatter(logging.Formatter):
    def __init__(self, *, service: str, environment: str) -> None:
        super().__init__()
        self._service = service
        self._environment = environment

    def format(self, record: logging.LogRecord) -> str:
        event: dict[str, Any] = {
            "timestamp": datetime.now(UTC).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": sanitize_text(record.getMessage()),
            "service": self._service,
            "environment": self._environment,
        }
        context_fields = {
            "correlation_id": correlation_id_var.get(),
            "task_id": task_id_var.get(),
            "task_name": task_name_var.get(),
            "retry_attempt": retry_attempt_var.get(),
        }
        event.update(
            {key: value for key, value in context_fields.items() if value is not None}
        )

        for key, value in record.__dict__.items():
            if key not in _STANDARD_RECORD_ATTRIBUTES and key not in event:
                event[key] = sanitize_value(key, value)

        if record.exc_info and record.exc_info[0] is not None:
            event["exception_type"] = record.exc_info[0].__name__
            event["exception"] = sanitize_text(self.formatException(record.exc_info))
        return json.dumps(event, ensure_ascii=False, separators=(",", ":"))


class TextFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        correlation_id = correlation_id_var.get()
        prefix = f"[{record.levelname}] {record.name}"
        if correlation_id:
            prefix += f" correlation_id={correlation_id}"
        return f"{prefix}: {sanitize_text(record.getMessage())}"


def _build_handler(settings: Settings) -> logging.Handler:
    handler = logging.StreamHandler()
    if settings.log_format == "json":
        handler.setFormatter(
            JsonFormatter(
                service=settings.service_name,
                environment=settings.environment,
            )
        )
    else:
        handler.setFormatter(TextFormatter())
    return handler


def configure_logger(logger: logging.Logger, settings: Settings) -> None:
    logger.handlers.clear()
    logger.addHandler(_build_handler(settings))
    logger.setLevel(settings.log_level)
    logger.propagate = False


def configure_logging(settings: Settings) -> None:
    handler = _build_handler(settings)

    root = logging.getLogger()
    root.handlers.clear()
    root.addHandler(handler)
    root.setLevel(settings.log_level)

    logging.getLogger("uvicorn.access").disabled = True
    for logger_name in ("uvicorn", "uvicorn.error"):
        uvicorn_logger = logging.getLogger(logger_name)
        uvicorn_logger.handlers.clear()
        uvicorn_logger.propagate = True

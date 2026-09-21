import re
from contextvars import ContextVar, Token
from uuid import uuid4

_CORRELATION_ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]*$")
CELERY_CORRELATION_HEADER = "x-correlation-id"

correlation_id_var: ContextVar[str | None] = ContextVar(
    "correlation_id",
    default=None,
)
task_id_var: ContextVar[str | None] = ContextVar("task_id", default=None)
task_name_var: ContextVar[str | None] = ContextVar("task_name", default=None)
retry_attempt_var: ContextVar[int | None] = ContextVar(
    "retry_attempt",
    default=None,
)


def is_valid_correlation_id(value: object, *, max_length: int = 128) -> bool:
    return (
        isinstance(value, str)
        and 1 <= len(value) <= max_length
        and _CORRELATION_ID_PATTERN.fullmatch(value) is not None
    )


def correlation_id_or_new(value: object, *, max_length: int = 128) -> str:
    if is_valid_correlation_id(value, max_length=max_length):
        return str(value)
    return str(uuid4())


def get_correlation_id() -> str | None:
    return correlation_id_var.get()


def set_correlation_id(value: str) -> Token[str | None]:
    return correlation_id_var.set(value)

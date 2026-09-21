from typing import Any

from celery import Task
from celery.utils.log import get_task_logger
from sqlalchemy.exc import OperationalError

from app.core.config import get_settings
from app.core.context import (
    CELERY_CORRELATION_HEADER,
    correlation_id_or_new,
    correlation_id_var,
    retry_attempt_var,
    task_id_var,
    task_name_var,
)

logger = get_task_logger(__name__)


class ResilientDatabaseTask(Task):
    """Retry only transient database operational failures with finite backoff."""

    abstract = True
    autoretry_for = (OperationalError,)
    retry_kwargs = {"max_retries": 3}
    retry_backoff = True
    retry_backoff_max = 60
    retry_jitter = True
    acks_late = True

    def apply_async(
        self,
        args: tuple[Any, ...] | None = None,
        kwargs: dict[str, Any] | None = None,
        **options: Any,
    ) -> Any:
        headers = dict(options.pop("headers", {}) or {})
        headers.setdefault(
            CELERY_CORRELATION_HEADER,
            correlation_id_or_new(
                correlation_id_var.get(),
                max_length=get_settings().correlation_id_max_length,
            ),
        )
        return super().apply_async(args=args, kwargs=kwargs, headers=headers, **options)

    def __call__(self, *args: Any, **kwargs: Any) -> Any:
        headers = getattr(self.request, "headers", None) or {}
        correlation_id = correlation_id_or_new(
            headers.get(CELERY_CORRELATION_HEADER),
            max_length=get_settings().correlation_id_max_length,
        )
        correlation_token = correlation_id_var.set(correlation_id)
        task_id_token = task_id_var.set(self.request.id)
        task_name_token = task_name_var.set(self.name)
        retry_token = retry_attempt_var.set(self.request.retries)
        try:
            return super().__call__(*args, **kwargs)
        finally:
            retry_attempt_var.reset(retry_token)
            task_name_var.reset(task_name_token)
            task_id_var.reset(task_id_token)
            correlation_id_var.reset(correlation_token)

    def on_retry(
        self,
        exc: BaseException,
        task_id: str,
        args: tuple[Any, ...],
        kwargs: dict[str, Any],
        einfo: object,
    ) -> None:
        logger.warning(
            "Task retry scheduled",
            extra={
                "task_name": self.name,
                "task_id": task_id,
                "retry_attempt": self.request.retries,
                "exception_type": type(exc).__name__,
                "correlation_id": correlation_id_or_new(
                    (getattr(self.request, "headers", None) or {}).get(
                        CELERY_CORRELATION_HEADER
                    ),
                    max_length=get_settings().correlation_id_max_length,
                ),
            },
        )
        super().on_retry(exc, task_id, args, kwargs, einfo)

    def on_failure(
        self,
        exc: BaseException,
        task_id: str,
        args: tuple[Any, ...],
        kwargs: dict[str, Any],
        einfo: object,
    ) -> None:
        logger.error(
            "Task failed permanently",
            extra={
                "task_name": self.name,
                "task_id": task_id,
                "retry_attempt": self.request.retries,
                "exception_type": type(exc).__name__,
                "correlation_id": correlation_id_or_new(
                    (getattr(self.request, "headers", None) or {}).get(
                        CELERY_CORRELATION_HEADER
                    ),
                    max_length=get_settings().correlation_id_max_length,
                ),
            },
        )
        super().on_failure(exc, task_id, args, kwargs, einfo)


def task_context(task: Task, **entity: int) -> dict[str, object]:
    return {
        "task_name": task.name,
        "task_id": task.request.id,
        "retry_attempt": task.request.retries,
        **entity,
    }

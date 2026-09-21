from typing import Any

from celery import Task
from celery.utils.log import get_task_logger
from sqlalchemy.exc import OperationalError

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

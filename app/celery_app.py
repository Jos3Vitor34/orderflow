import logging

from celery import Celery
from celery.signals import (
    after_setup_logger,
    after_setup_task_logger,
    worker_process_init,
)

from app.core.config import get_settings
from app.core.logging import configure_logger, configure_logging

settings = get_settings()
configure_logging(settings)

celery_app = Celery(
    "orderflow",
    broker=settings.celery_broker_url,
    backend=settings.celery_result_backend,
    include=["app.tasks"],
)
celery_app.conf.update(
    accept_content=["json"],
    broker_connection_max_retries=10,
    broker_connection_retry_on_startup=True,
    enable_utc=True,
    result_expires=3600,
    result_serializer="json",
    task_always_eager=settings.celery_task_always_eager,
    task_default_queue="orderflow",
    task_eager_propagates=settings.celery_task_eager_propagates,
    task_serializer="json",
    timezone="UTC",
    worker_prefetch_multiplier=1,
    worker_hijack_root_logger=False,
)


@after_setup_logger.connect(weak=False)
@after_setup_task_logger.connect(weak=False)
def configure_celery_logging(logger: logging.Logger, **_: object) -> None:
    """Keep Celery and task events on the shared JSON logging strategy."""
    configure_logger(logger, settings)


@worker_process_init.connect(weak=False)
def dispose_inherited_database_connections(**_: object) -> None:
    """Ensure a prefork worker never reuses a parent SQLAlchemy connection."""
    from app.db.session import engine

    engine.dispose(close=False)

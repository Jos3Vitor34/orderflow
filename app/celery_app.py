from celery import Celery
from celery.signals import worker_process_init

from app.core.config import get_settings

settings = get_settings()

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
)


@worker_process_init.connect(weak=False)
def dispose_inherited_database_connections(**_: object) -> None:
    """Ensure a prefork worker never reuses a parent SQLAlchemy connection."""
    from app.db.session import engine

    engine.dispose(close=False)

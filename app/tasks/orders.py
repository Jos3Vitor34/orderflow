from celery.utils.log import get_task_logger
from sqlalchemy.exc import SQLAlchemyError

from app.celery_app import celery_app
from app.db.session import SessionLocal
from app.models.order import Order, OrderStatus
from app.tasks.base import ResilientDatabaseTask, task_context

logger = get_task_logger(__name__)

CONFIRMATION_COMPATIBLE_STATUSES = frozenset(
    {OrderStatus.CONFIRMED, OrderStatus.SHIPPED, OrderStatus.DELIVERED}
)


@celery_app.task(
    bind=True,
    base=ResilientDatabaseTask,
    name="orderflow.tasks.send_order_confirmation",
    ignore_result=True,
)
def send_order_confirmation(
    self: ResilientDatabaseTask,
    order_id: int,
) -> dict[str, object]:
    context = task_context(self, order_id=order_id)
    try:
        with SessionLocal() as session:
            try:
                order = session.get(Order, order_id)
                if order is None:
                    logger.info(
                        "Order confirmation skipped: order not found", extra=context
                    )
                    return {
                        "status": "skipped",
                        "reason": "not_found",
                        "order_id": order_id,
                    }
                if order.status not in CONFIRMATION_COMPATIBLE_STATUSES:
                    logger.info(
                        "Order confirmation skipped: incompatible state",
                        extra=context | {"order_status": order.status.value},
                    )
                    return {
                        "status": "skipped",
                        "reason": "incompatible_state",
                        "order_id": order_id,
                    }
                logger.info("Order confirmation sent", extra=context)
                return {"status": "sent", "order_id": order_id}
            except SQLAlchemyError:
                session.rollback()
                raise
    except SQLAlchemyError:
        logger.warning("Order confirmation database failure", extra=context)
        raise

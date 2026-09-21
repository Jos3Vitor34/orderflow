from celery.utils.log import get_task_logger
from sqlalchemy import func, select
from sqlalchemy.exc import SQLAlchemyError

from app.celery_app import celery_app
from app.db.session import SessionLocal
from app.models.order import Order, OrderStatus
from app.tasks.base import ResilientDatabaseTask, task_context

logger = get_task_logger(__name__)


@celery_app.task(
    bind=True,
    base=ResilientDatabaseTask,
    name="orderflow.tasks.generate_order_report",
)
def generate_order_report(
    self: ResilientDatabaseTask,
) -> dict[str, int]:
    context = task_context(self)
    try:
        with SessionLocal() as session:
            try:
                rows = session.execute(
                    select(Order.status, func.count(Order.id)).group_by(Order.status)
                )
                counts = {status: count for status, count in rows}
                report = {
                    "total_orders": sum(counts.values()),
                    "pending_orders": counts.get(OrderStatus.PENDING, 0),
                    "processing_orders": counts.get(OrderStatus.PROCESSING, 0),
                    "confirmed_orders": counts.get(OrderStatus.CONFIRMED, 0),
                    "shipped_orders": counts.get(OrderStatus.SHIPPED, 0),
                    "delivered_orders": counts.get(OrderStatus.DELIVERED, 0),
                    "cancelled_orders": counts.get(OrderStatus.CANCELLED, 0),
                }
                logger.info("Order report generated", extra=context)
                return report
            except SQLAlchemyError:
                session.rollback()
                raise
    except SQLAlchemyError:
        logger.warning("Order report database failure", extra=context)
        raise

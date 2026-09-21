from celery.utils.log import get_task_logger
from sqlalchemy.exc import SQLAlchemyError

from app.celery_app import celery_app
from app.db.session import SessionLocal
from app.models.payment import Payment, PaymentStatus
from app.tasks.base import ResilientDatabaseTask, task_context

logger = get_task_logger(__name__)

NOTIFICATION_COMPATIBLE_STATUSES = frozenset(
    {
        PaymentStatus.APPROVED,
        PaymentStatus.FAILED,
        PaymentStatus.PARTIALLY_REFUNDED,
        PaymentStatus.REFUNDED,
    }
)


@celery_app.task(
    bind=True,
    base=ResilientDatabaseTask,
    name="orderflow.tasks.send_payment_notification",
    ignore_result=True,
)
def send_payment_notification(
    self: ResilientDatabaseTask,
    payment_id: int,
) -> dict[str, object]:
    context = task_context(self, payment_id=payment_id)
    try:
        with SessionLocal() as session:
            try:
                payment = session.get(Payment, payment_id)
                if payment is None:
                    logger.info(
                        "Payment notification skipped: payment not found", extra=context
                    )
                    return {
                        "status": "skipped",
                        "reason": "not_found",
                        "payment_id": payment_id,
                    }
                if payment.status not in NOTIFICATION_COMPATIBLE_STATUSES:
                    logger.info(
                        "Payment notification skipped: incompatible state",
                        extra=context | {"payment_status": payment.status.value},
                    )
                    return {
                        "status": "skipped",
                        "reason": "incompatible_state",
                        "payment_id": payment_id,
                    }
                logger.info("Payment notification sent", extra=context)
                return {"status": "sent", "payment_id": payment_id}
            except SQLAlchemyError:
                session.rollback()
                raise
    except SQLAlchemyError:
        logger.warning("Payment notification database failure", extra=context)
        raise

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models.order import Order, OrderStatus
from app.models.payment import Payment, PaymentStatus

SUCCESSFUL_PAYMENT_STATUSES = (
    PaymentStatus.APPROVED,
    PaymentStatus.PARTIALLY_REFUNDED,
    PaymentStatus.REFUNDED,
)


@dataclass(frozen=True)
class StatisticsAggregate:
    order_total: int
    order_counts: dict[OrderStatus, int]
    order_amount: Decimal
    order_average: Decimal
    payment_total: int
    payment_counts: dict[PaymentStatus, int]
    payment_amount: Decimal
    successful_payment_amount: Decimal


class StatisticsRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    @staticmethod
    def _period_conditions(
        created_at: object,
        *,
        start: datetime | None,
        end: datetime | None,
    ) -> list[object]:
        conditions: list[object] = []
        if start is not None:
            conditions.append(created_at >= start)
        if end is not None:
            conditions.append(created_at < end)
        return conditions

    def overview(
        self,
        *,
        start: datetime | None,
        end: datetime | None,
    ) -> StatisticsAggregate:
        order_conditions = self._period_conditions(
            Order.created_at,
            start=start,
            end=end,
        )
        order_summary = self._session.execute(
            select(
                func.count(Order.id),
                func.coalesce(func.sum(Order.total_amount), Decimal("0.00")),
                func.coalesce(func.avg(Order.total_amount), Decimal("0.00")),
            ).where(*order_conditions)
        ).one()
        order_rows = self._session.execute(
            select(Order.status, func.count(Order.id))
            .where(*order_conditions)
            .group_by(Order.status)
        )

        payment_conditions = self._period_conditions(
            Payment.created_at,
            start=start,
            end=end,
        )
        payment_summary = self._session.execute(
            select(
                func.count(Payment.id),
                func.coalesce(func.sum(Payment.amount), Decimal("0.00")),
            ).where(*payment_conditions)
        ).one()
        payment_rows = self._session.execute(
            select(Payment.status, func.count(Payment.id))
            .where(*payment_conditions)
            .group_by(Payment.status)
        )
        successful_payment_amount = self._session.scalar(
            select(func.coalesce(func.sum(Payment.amount), Decimal("0.00"))).where(
                *payment_conditions,
                Payment.status.in_(SUCCESSFUL_PAYMENT_STATUSES),
            )
        )

        return StatisticsAggregate(
            order_total=int(order_summary[0]),
            order_counts={status: int(count) for status, count in order_rows},
            order_amount=Decimal(order_summary[1]),
            order_average=Decimal(order_summary[2]),
            payment_total=int(payment_summary[0]),
            payment_counts={status: int(count) for status, count in payment_rows},
            payment_amount=Decimal(payment_summary[1]),
            successful_payment_amount=Decimal(successful_payment_amount or 0),
        )

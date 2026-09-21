from datetime import UTC, datetime
from decimal import ROUND_HALF_UP, Decimal

from app.models.order import OrderStatus
from app.models.payment import PaymentStatus
from app.repositories.statistics import StatisticsRepository
from app.schemas.statistics import (
    OperationalStatistics,
    OrderStatistics,
    PaymentStatistics,
    StatisticsOverviewResponse,
)

MONEY_QUANTUM = Decimal("0.01")
RATE_QUANTUM = Decimal("0.01")


class InvalidStatisticsPeriodError(Exception):
    """Raised when an overview interval has invalid or naive bounds."""


class StatisticsService:
    def __init__(self, repository: StatisticsRepository) -> None:
        self._repository = repository

    @staticmethod
    def _normalize_bound(value: datetime | None) -> datetime | None:
        if value is None:
            return None
        if value.tzinfo is None or value.utcoffset() is None:
            raise InvalidStatisticsPeriodError
        return value.astimezone(UTC)

    def overview(
        self,
        *,
        start: datetime | None,
        end: datetime | None,
    ) -> StatisticsOverviewResponse:
        start = self._normalize_bound(start)
        end = self._normalize_bound(end)
        if start is not None and end is not None and start > end:
            raise InvalidStatisticsPeriodError

        aggregate = self._repository.overview(start=start, end=end)
        order_counts = {
            status: aggregate.order_counts.get(status, 0) for status in OrderStatus
        }
        payment_counts = {
            status: aggregate.payment_counts.get(status, 0) for status in PaymentStatus
        }
        successful_count = sum(
            payment_counts[status]
            for status in (
                PaymentStatus.APPROVED,
                PaymentStatus.PARTIALLY_REFUNDED,
                PaymentStatus.REFUNDED,
            )
        )
        completed_count = successful_count + payment_counts[PaymentStatus.FAILED]
        success_rate = (
            (Decimal(successful_count) * 100 / Decimal(completed_count)).quantize(
                RATE_QUANTUM,
                rounding=ROUND_HALF_UP,
            )
            if completed_count
            else Decimal("0.00")
        )

        return StatisticsOverviewResponse(
            orders=OrderStatistics(
                total=aggregate.order_total,
                by_status=order_counts,
                total_amount=aggregate.order_amount.quantize(MONEY_QUANTUM),
                average_ticket=aggregate.order_average.quantize(MONEY_QUANTUM),
                created_in_period=aggregate.order_total,
            ),
            payments=PaymentStatistics(
                total=aggregate.payment_total,
                by_status=payment_counts,
                total_processed_amount=aggregate.payment_amount.quantize(MONEY_QUANTUM),
                successful_amount=aggregate.successful_payment_amount.quantize(
                    MONEY_QUANTUM
                ),
                successful_count=successful_count,
                success_rate=success_rate,
            ),
            operational=OperationalStatistics(
                pending_orders=order_counts[OrderStatus.PENDING],
                processing_orders=order_counts[OrderStatus.PROCESSING],
                confirmed_orders=order_counts[OrderStatus.CONFIRMED],
                cancelled_orders=order_counts[OrderStatus.CANCELLED],
            ),
            generated_at=datetime.now(UTC),
        )

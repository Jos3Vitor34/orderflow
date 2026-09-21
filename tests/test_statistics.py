from collections.abc import Generator
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient

from app.api.dependencies import (
    get_current_user,
    get_statistics_service,
)
from app.main import app
from app.models.order import OrderStatus
from app.models.payment import PaymentStatus
from app.models.user import User, UserRole
from app.repositories.statistics import StatisticsAggregate
from app.services.statistics import InvalidStatisticsPeriodError, StatisticsService


class StatisticsRepositoryDouble:
    def __init__(self, aggregate: StatisticsAggregate) -> None:
        self.aggregate = aggregate
        self.calls: list[tuple[datetime | None, datetime | None]] = []

    def overview(
        self, *, start: datetime | None, end: datetime | None
    ) -> StatisticsAggregate:
        self.calls.append((start, end))
        return self.aggregate


def aggregate() -> StatisticsAggregate:
    return StatisticsAggregate(
        order_total=4,
        order_counts={
            OrderStatus.PENDING: 1,
            OrderStatus.PROCESSING: 1,
            OrderStatus.CONFIRMED: 2,
        },
        order_amount=Decimal("100.01"),
        order_average=Decimal("25.0025"),
        payment_total=5,
        payment_counts={
            PaymentStatus.PENDING: 1,
            PaymentStatus.APPROVED: 2,
            PaymentStatus.FAILED: 1,
            PaymentStatus.REFUNDED: 1,
        },
        payment_amount=Decimal("123.456"),
        successful_payment_amount=Decimal("99.999"),
    )


def test_statistics_service_returns_all_statuses_decimal_precision_and_rate() -> None:
    repository = StatisticsRepositoryDouble(aggregate())
    before = datetime.now(UTC)
    result = StatisticsService(repository).overview(start=None, end=None)  # type: ignore[arg-type]

    assert result.orders.total == 4
    assert result.orders.created_in_period == 4
    assert result.orders.total_amount == Decimal("100.01")
    assert result.orders.average_ticket == Decimal("25.00")
    assert result.orders.by_status[OrderStatus.CANCELLED] == 0
    assert result.payments.total_processed_amount == Decimal("123.46")
    assert result.payments.successful_amount == Decimal("100.00")
    assert result.payments.successful_count == 3
    assert result.payments.success_rate == Decimal("75.00")
    assert result.payments.by_status[PaymentStatus.PARTIALLY_REFUNDED] == 0
    assert result.operational.cancelled_orders == 0
    assert result.generated_at >= before


def test_statistics_service_empty_database_is_stable() -> None:
    empty = StatisticsAggregate(
        order_total=0,
        order_counts={},
        order_amount=Decimal("0"),
        order_average=Decimal("0"),
        payment_total=0,
        payment_counts={},
        payment_amount=Decimal("0"),
        successful_payment_amount=Decimal("0"),
    )
    result = StatisticsService(StatisticsRepositoryDouble(empty)).overview(  # type: ignore[arg-type]
        start=None,
        end=None,
    )
    assert result.orders.total == 0
    assert set(result.orders.by_status) == set(OrderStatus)
    assert set(result.payments.by_status) == set(PaymentStatus)
    assert result.payments.success_rate == Decimal("0.00")


def test_statistics_normalizes_timezone_and_rejects_invalid_intervals() -> None:
    repository = StatisticsRepositoryDouble(aggregate())
    service = StatisticsService(repository)  # type: ignore[arg-type]
    start = datetime(2026, 1, 1, tzinfo=UTC)
    end = start + timedelta(days=1)
    service.overview(start=start, end=end)

    assert repository.calls == [(start, end)]
    with pytest.raises(InvalidStatisticsPeriodError):
        service.overview(start=end, end=start)
    with pytest.raises(InvalidStatisticsPeriodError):
        service.overview(start=datetime(2026, 1, 1), end=None)


@pytest.fixture
def statistics_client() -> Generator[
    tuple[TestClient, User, StatisticsRepositoryDouble]
]:
    now = datetime.now(UTC)
    user = User(
        id=1,
        full_name="Statistics Viewer",
        email="stats@example.com",
        hashed_password="not-used",
        role=UserRole.VIEWER,
        is_active=True,
        created_at=now,
        updated_at=now,
    )
    repository = StatisticsRepositoryDouble(aggregate())
    service = StatisticsService(repository)  # type: ignore[arg-type]
    app.dependency_overrides[get_current_user] = lambda: user
    app.dependency_overrides[get_statistics_service] = lambda: service
    with TestClient(app) as client:
        yield client, user, repository
    app.dependency_overrides.clear()


def test_viewer_can_query_statistics_with_half_open_time_filter(
    statistics_client: tuple[TestClient, User, StatisticsRepositoryDouble],
) -> None:
    client, _, repository = statistics_client
    response = client.get(
        "/api/v1/statistics/overview",
        params={
            "start": "2026-01-01T00:00:00-03:00",
            "end": "2026-02-01T00:00:00-03:00",
        },
    )

    assert response.status_code == 200
    assert repository.calls[0] == (
        datetime(2026, 1, 1, 3, tzinfo=UTC),
        datetime(2026, 2, 1, 3, tzinfo=UTC),
    )
    assert "email" not in response.text
    assert "customer" not in response.text


def test_statistics_rejects_naive_or_reversed_interval(
    statistics_client: tuple[TestClient, User, StatisticsRepositoryDouble],
) -> None:
    client, _, _ = statistics_client
    naive = client.get("/api/v1/statistics/overview?start=2026-01-01T00:00:00")
    reversed_period = client.get(
        "/api/v1/statistics/overview",
        params={
            "start": "2026-02-01T00:00:00Z",
            "end": "2026-01-01T00:00:00Z",
        },
    )
    assert naive.status_code == 422
    assert reversed_period.status_code == 422

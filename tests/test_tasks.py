import json
import logging
from types import SimpleNamespace
from typing import Any

import pytest
from sqlalchemy.exc import OperationalError

from app.celery_app import celery_app
from app.models.order import OrderStatus
from app.models.payment import PaymentStatus
from app.tasks import (
    generate_order_report,
    send_order_confirmation,
    send_payment_notification,
)


class SessionDouble:
    def __init__(
        self,
        *,
        entities: dict[tuple[type[object], int], object] | None = None,
        rows: list[tuple[OrderStatus, int]] | None = None,
        failure: Exception | None = None,
    ) -> None:
        self.entities = entities or {}
        self.rows = rows or []
        self.failure = failure
        self.closed = False
        self.get_calls = 0
        self.rollback_calls = 0

    def __enter__(self) -> "SessionDouble":
        return self

    def __exit__(self, *_: object) -> None:
        self.closed = True

    def get(self, model: type[object], entity_id: int) -> object | None:
        self.get_calls += 1
        if self.failure is not None:
            raise self.failure
        return self.entities.get((model, entity_id))

    def execute(self, statement: object) -> list[tuple[OrderStatus, int]]:
        if self.failure is not None:
            raise self.failure
        return self.rows

    def rollback(self) -> None:
        self.rollback_calls += 1


class SessionFactoryDouble:
    def __init__(self, builder: Any) -> None:
        self.builder = builder
        self.sessions: list[SessionDouble] = []

    def __call__(self) -> SessionDouble:
        session = self.builder()
        self.sessions.append(session)
        return session


def task_result(task: object, *args: object) -> object:
    return task.apply(args=args, throw=True).result  # type: ignore[attr-defined]


def test_celery_registers_namespaced_tasks() -> None:
    assert celery_app.tasks[send_order_confirmation.name].name == (
        send_order_confirmation.name
    )
    assert celery_app.tasks[send_payment_notification.name].name == (
        send_payment_notification.name
    )
    assert (
        celery_app.tasks[generate_order_report.name].name == generate_order_report.name
    )
    assert {
        send_order_confirmation.name,
        send_payment_notification.name,
        generate_order_report.name,
    } == {
        "orderflow.tasks.send_order_confirmation",
        "orderflow.tasks.send_payment_notification",
        "orderflow.tasks.generate_order_report",
    }


def test_celery_uses_json_utc_and_isolated_test_transport() -> None:
    assert celery_app.conf.task_serializer == "json"
    assert celery_app.conf.result_serializer == "json"
    assert celery_app.conf.accept_content == ["json"]
    assert celery_app.conf.enable_utc is True
    assert celery_app.conf.timezone == "UTC"
    assert celery_app.conf.task_always_eager is True
    assert celery_app.conf.task_eager_propagates is True
    assert celery_app.conf.broker_url == "memory://"
    assert celery_app.conf.result_backend == "cache+memory://"


@pytest.mark.parametrize(
    ("task", "args"),
    [
        (send_order_confirmation, (1,)),
        (send_payment_notification, (2,)),
        (generate_order_report, ()),
    ],
)
def test_task_arguments_are_json_safe(task: object, args: tuple[int, ...]) -> None:
    signature = task.signature(args=args)  # type: ignore[attr-defined]
    assert json.loads(json.dumps(signature.args)) == list(args)


def test_notification_tasks_ignore_results_and_all_tasks_are_idempotent() -> None:
    assert send_order_confirmation.ignore_result is True
    assert send_payment_notification.ignore_result is True
    assert generate_order_report.ignore_result is False
    assert send_order_confirmation.acks_late is True
    assert send_payment_notification.acks_late is True
    assert generate_order_report.acks_late is True


def test_order_confirmation_loads_current_order_logs_and_closes_session(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    from app.models.order import Order
    from app.tasks import orders

    order = SimpleNamespace(status=OrderStatus.CONFIRMED)
    factory = SessionFactoryDouble(lambda: SessionDouble(entities={(Order, 7): order}))
    monkeypatch.setattr(orders, "SessionLocal", factory)

    with caplog.at_level(logging.INFO):
        result = task_result(send_order_confirmation, 7)

    assert result == {"status": "sent", "order_id": 7}
    assert "Order confirmation sent" in caplog.text
    assert len(factory.sessions) == 1
    assert factory.sessions[0].closed is True


@pytest.mark.parametrize(
    ("status", "reason"),
    [
        (None, "not_found"),
        (OrderStatus.PENDING, "incompatible_state"),
    ],
)
def test_order_confirmation_skips_permanent_conditions_without_retry(
    monkeypatch: pytest.MonkeyPatch,
    status: OrderStatus | None,
    reason: str,
) -> None:
    from app.models.order import Order
    from app.tasks import orders

    entities = {} if status is None else {(Order, 7): SimpleNamespace(status=status)}
    factory = SessionFactoryDouble(lambda: SessionDouble(entities=entities))
    monkeypatch.setattr(orders, "SessionLocal", factory)

    result = task_result(send_order_confirmation, 7)

    assert result == {"status": "skipped", "reason": reason, "order_id": 7}
    assert len(factory.sessions) == 1
    assert factory.sessions[0].closed is True


def test_payment_notification_loads_current_payment_logs_and_closes_session(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    from app.models.payment import Payment
    from app.tasks import payments

    payment = SimpleNamespace(status=PaymentStatus.APPROVED)
    factory = SessionFactoryDouble(
        lambda: SessionDouble(entities={(Payment, 9): payment})
    )
    monkeypatch.setattr(payments, "SessionLocal", factory)

    with caplog.at_level(logging.INFO):
        result = task_result(send_payment_notification, 9)

    assert result == {"status": "sent", "payment_id": 9}
    assert "Payment notification sent" in caplog.text
    assert factory.sessions[0].closed is True


@pytest.mark.parametrize(
    ("status", "reason"),
    [
        (None, "not_found"),
        (PaymentStatus.PENDING, "incompatible_state"),
    ],
)
def test_payment_notification_skips_permanent_conditions_without_retry(
    monkeypatch: pytest.MonkeyPatch,
    status: PaymentStatus | None,
    reason: str,
) -> None:
    from app.models.payment import Payment
    from app.tasks import payments

    entities = {} if status is None else {(Payment, 9): SimpleNamespace(status=status)}
    factory = SessionFactoryDouble(lambda: SessionDouble(entities=entities))
    monkeypatch.setattr(payments, "SessionLocal", factory)

    result = task_result(send_payment_notification, 9)

    assert result == {"status": "skipped", "reason": reason, "payment_id": 9}
    assert len(factory.sessions) == 1
    assert factory.sessions[0].closed is True


def test_notification_logs_do_not_expose_sensitive_values(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    from app.models.payment import Payment
    from app.tasks import payments

    sensitive_values = ["sk_test_secret", "whsec_secret", "client_secret_value"]
    factory = SessionFactoryDouble(
        lambda: SessionDouble(
            entities={
                (Payment, 9): SimpleNamespace(
                    status=PaymentStatus.APPROVED,
                    provider_reference=sensitive_values[0],
                )
            }
        )
    )
    monkeypatch.setattr(payments, "SessionLocal", factory)

    with caplog.at_level(logging.INFO):
        task_result(send_payment_notification, 9)

    assert all(value not in caplog.text for value in sensitive_values)


def test_order_report_is_json_safe_read_only_and_repeatable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.tasks import reports

    rows = [
        (OrderStatus.PENDING, 2),
        (OrderStatus.CONFIRMED, 3),
        (OrderStatus.DELIVERED, 1),
    ]
    factory = SessionFactoryDouble(lambda: SessionDouble(rows=rows))
    monkeypatch.setattr(reports, "SessionLocal", factory)

    first = task_result(generate_order_report)
    second = task_result(generate_order_report)

    assert first == second
    assert first == {
        "total_orders": 6,
        "pending_orders": 2,
        "processing_orders": 0,
        "confirmed_orders": 3,
        "shipped_orders": 0,
        "delivered_orders": 1,
        "cancelled_orders": 0,
    }
    assert json.loads(json.dumps(first)) == first
    assert len(factory.sessions) == 2
    assert all(session.closed for session in factory.sessions)


def test_transient_database_failure_retries_with_finite_backoff(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.tasks import orders

    failure = OperationalError("SELECT", {}, OSError("database unavailable"))
    factory = SessionFactoryDouble(lambda: SessionDouble(failure=failure))
    monkeypatch.setattr(orders, "SessionLocal", factory)
    monkeypatch.setitem(celery_app.conf, "task_eager_propagates", False)

    with pytest.raises(OperationalError):
        send_order_confirmation.apply(args=(7,)).get(propagate=True)

    assert len(factory.sessions) == 4
    assert all(session.closed for session in factory.sessions)
    assert all(session.rollback_calls == 1 for session in factory.sessions)
    assert send_order_confirmation.max_retries == 3
    assert send_order_confirmation.retry_backoff is True
    assert send_order_confirmation.retry_backoff_max == 60
    assert send_order_confirmation.retry_jitter is True


def test_permanent_programming_failure_is_not_retried(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.tasks import orders

    factory = SessionFactoryDouble(
        lambda: SessionDouble(failure=ValueError("permanent invalid data"))
    )
    monkeypatch.setattr(orders, "SessionLocal", factory)

    with pytest.raises(ValueError, match="permanent invalid data"):
        send_order_confirmation.apply(args=(7,), throw=True)

    assert len(factory.sessions) == 1
    assert factory.sessions[0].closed is True

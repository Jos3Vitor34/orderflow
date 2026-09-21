import logging
from datetime import UTC, datetime
from decimal import Decimal

import pytest

from app.models.order import Order, OrderStatus
from app.models.order_item import OrderItem
from app.models.payment import Payment, PaymentStatus
from app.models.product import Product
from app.repositories.order import OrderPersistenceError
from app.repositories.payment import PaymentConstraintError
from app.schemas.order import OrderUpdate
from app.schemas.payment import PaymentUpdate
from app.services.order import OrderService
from app.services.payment import PaymentService


class ConfirmationRepositoryDouble:
    def __init__(self) -> None:
        now = datetime.now(UTC)
        self.order = Order(
            id=1,
            customer_id=1,
            status=OrderStatus.PROCESSING,
            total_amount=Decimal("10.00"),
            created_at=now,
            updated_at=now,
        )
        self.order.items = [
            OrderItem(
                id=1,
                order_id=1,
                product_id=1,
                quantity=1,
                unit_price=Decimal("10.00"),
            )
        ]
        self.product = Product(
            id=1,
            sku="TASK-PRODUCT",
            name="Task product",
            price=Decimal("10.00"),
            stock=2,
            is_active=True,
            created_at=now,
            updated_at=now,
        )
        self.events: list[str] = []
        self.fail_update = False

    def get_by_id_for_update(self, order_id: int) -> Order | None:
        return self.order if order_id == self.order.id else None

    def get_products_by_ids_for_update(self, product_ids: set[int]) -> list[Product]:
        return [self.product] if product_ids == {self.product.id} else []

    def update_status(self, order: Order, new_status: OrderStatus) -> Order:
        if self.fail_update:
            raise OrderPersistenceError
        order.status = new_status
        self.events.append("commit")
        return order

    def rollback(self) -> None:
        self.events.append("rollback")


class PaymentRepositoryDouble:
    def __init__(self) -> None:
        now = datetime.now(UTC)
        self.payment = Payment(
            id=2,
            order_id=1,
            provider="manual",
            provider_reference="safe-reference",
            amount=Decimal("10.00"),
            status=PaymentStatus.PENDING,
            created_at=now,
            updated_at=now,
        )
        self.events: list[str] = []
        self.fail_update = False

    def get_by_id_for_update(self, payment_id: int) -> Payment | None:
        return self.payment if payment_id == self.payment.id else None

    def update_status(self, payment: Payment, new_status: PaymentStatus) -> Payment:
        if self.fail_update:
            raise PaymentConstraintError
        payment.status = new_status
        self.events.append("commit")
        return payment


def test_order_confirmation_is_published_only_after_commit() -> None:
    repository = ConfirmationRepositoryDouble()

    def publish(order_id: int) -> None:
        assert repository.events == ["commit"]
        assert repository.order.status == OrderStatus.CONFIRMED
        repository.events.append(f"publish:{order_id}")

    response = OrderService(
        repository,  # type: ignore[arg-type]
        confirmation_publisher=publish,
    ).update(1, OrderUpdate(status=OrderStatus.CONFIRMED))

    assert response.status == OrderStatus.CONFIRMED
    assert repository.events == ["commit", "publish:1"]


def test_order_rollback_does_not_publish() -> None:
    repository = ConfirmationRepositoryDouble()
    repository.fail_update = True
    published: list[int] = []
    service = OrderService(
        repository,  # type: ignore[arg-type]
        confirmation_publisher=published.append,
    )

    with pytest.raises(OrderPersistenceError):
        service.update(1, OrderUpdate(status=OrderStatus.CONFIRMED))

    assert published == []
    assert repository.events == ["rollback"]


def test_idempotent_order_transition_does_not_publish() -> None:
    repository = ConfirmationRepositoryDouble()
    repository.order.status = OrderStatus.CONFIRMED
    published: list[int] = []

    OrderService(
        repository,  # type: ignore[arg-type]
        confirmation_publisher=published.append,
    ).update(1, OrderUpdate(status=OrderStatus.CONFIRMED))

    assert published == []
    assert repository.events == ["rollback"]


def test_order_publication_failure_preserves_committed_domain_result(
    caplog: pytest.LogCaptureFixture,
) -> None:
    repository = ConfirmationRepositoryDouble()

    def fail_publication(order_id: int) -> None:
        raise ConnectionError(
            f"broker redis://:sensitive-password@redis for order {order_id}"
        )

    with caplog.at_level(logging.ERROR):
        response = OrderService(
            repository,  # type: ignore[arg-type]
            confirmation_publisher=fail_publication,
        ).update(1, OrderUpdate(status=OrderStatus.CONFIRMED))

    assert response.status == OrderStatus.CONFIRMED
    assert repository.events == ["commit"]
    assert "publication failed after commit" in caplog.text
    assert "sensitive-password" not in caplog.text


def test_payment_notification_is_published_only_after_commit() -> None:
    repository = PaymentRepositoryDouble()

    def publish(payment_id: int) -> None:
        assert repository.events == ["commit"]
        assert repository.payment.status == PaymentStatus.APPROVED
        repository.events.append(f"publish:{payment_id}")

    payment = PaymentService(
        repository,  # type: ignore[arg-type]
        notification_publisher=publish,
    ).update(2, PaymentUpdate(status=PaymentStatus.APPROVED))

    assert payment.status == PaymentStatus.APPROVED
    assert repository.events == ["commit", "publish:2"]


def test_payment_persistence_failure_does_not_publish() -> None:
    repository = PaymentRepositoryDouble()
    repository.fail_update = True
    published: list[int] = []

    with pytest.raises(PaymentConstraintError):
        PaymentService(
            repository,  # type: ignore[arg-type]
            notification_publisher=published.append,
        ).update(2, PaymentUpdate(status=PaymentStatus.APPROVED))

    assert published == []


def test_idempotent_payment_transition_does_not_publish() -> None:
    repository = PaymentRepositoryDouble()
    repository.payment.status = PaymentStatus.APPROVED
    published: list[int] = []

    PaymentService(
        repository,  # type: ignore[arg-type]
        notification_publisher=published.append,
    ).update(2, PaymentUpdate(status=PaymentStatus.APPROVED))

    assert published == []
    assert repository.events == []


def test_payment_publication_failure_preserves_committed_domain_result(
    caplog: pytest.LogCaptureFixture,
) -> None:
    repository = PaymentRepositoryDouble()

    def fail_publication(payment_id: int) -> None:
        raise ConnectionError(f"broker unavailable for payment {payment_id}")

    with caplog.at_level(logging.ERROR):
        payment = PaymentService(
            repository,  # type: ignore[arg-type]
            notification_publisher=fail_publication,
        ).update(2, PaymentUpdate(status=PaymentStatus.APPROVED))

    assert payment.status == PaymentStatus.APPROVED
    assert repository.events == ["commit"]
    assert "publication failed after commit" in caplog.text

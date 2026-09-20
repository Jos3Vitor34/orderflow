from dataclasses import dataclass
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.models.order import Order, OrderStatus
from app.models.payment import Payment
from app.repositories.payment import raise_write_error


@dataclass(frozen=True)
class OrderPaymentSnapshot:
    id: int
    status: OrderStatus
    total_amount: Decimal


class StripePaymentRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    def get_order_snapshot(self, order_id: int) -> OrderPaymentSnapshot | None:
        statement = select(Order.id, Order.status, Order.total_amount).where(
            Order.id == order_id
        )
        row = self._session.execute(statement).one_or_none()
        self._session.rollback()
        if row is None:
            return None
        return OrderPaymentSnapshot(
            id=row.id,
            status=row.status,
            total_amount=row.total_amount,
        )

    def get_order_for_update(self, order_id: int) -> Order | None:
        statement = select(Order).where(Order.id == order_id).with_for_update()
        return self._session.scalar(statement)

    def get_by_provider_reference(self, provider_reference: str) -> Payment | None:
        statement = select(Payment).where(
            Payment.provider_reference == provider_reference
        )
        return self._session.scalar(statement)

    def create(
        self,
        *,
        order_id: int,
        provider_reference: str,
        amount: Decimal,
    ) -> Payment:
        payment = Payment(
            order_id=order_id,
            provider="stripe",
            provider_reference=provider_reference,
            amount=amount,
        )
        self._session.add(payment)
        try:
            self._session.commit()
        except IntegrityError as exc:
            self._session.rollback()
            raise_write_error(exc)
        self._session.refresh(payment)
        return payment

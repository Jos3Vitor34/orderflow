from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.models.order import Order
from app.models.payment import Payment, PaymentStatus


class DuplicatePaymentReferenceError(Exception):
    """Raised when a provider reference already identifies a payment."""


class PaymentConstraintError(Exception):
    """Raised when payment data violates a database constraint."""


def raise_write_error(exc: IntegrityError) -> None:
    diagnostic = getattr(exc.orig, "diag", None)
    if getattr(diagnostic, "constraint_name", None) == (
        "payments_provider_reference_key"
    ):
        raise DuplicatePaymentReferenceError from exc
    raise PaymentConstraintError from exc


class PaymentRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    def get_order_for_payment(self, order_id: int) -> Order | None:
        statement = select(Order).where(Order.id == order_id).with_for_update()
        return self._session.scalar(statement)

    def get_by_id(self, payment_id: int) -> Payment | None:
        return self._session.get(Payment, payment_id)

    def get_by_id_for_update(self, payment_id: int) -> Payment | None:
        statement = select(Payment).where(Payment.id == payment_id).with_for_update()
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
        provider: str,
        provider_reference: str | None,
        amount: Decimal,
    ) -> Payment:
        payment = Payment(
            order_id=order_id,
            provider=provider,
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

    def list_page(self, *, offset: int, limit: int) -> tuple[list[Payment], int]:
        total = self._session.scalar(select(func.count()).select_from(Payment)) or 0
        statement = (
            select(Payment).order_by(Payment.id.asc()).offset(offset).limit(limit)
        )
        return list(self._session.scalars(statement).all()), total

    def update_status(self, payment: Payment, new_status: PaymentStatus) -> Payment:
        payment.status = new_status
        try:
            self._session.commit()
        except IntegrityError as exc:
            self._session.rollback()
            raise_write_error(exc)
        self._session.refresh(payment)
        return payment

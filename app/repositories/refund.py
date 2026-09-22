from datetime import UTC, datetime
from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.models.payment import Payment, PaymentStatus
from app.models.refund import Refund, RefundStatus


class DuplicateRefundReferenceError(Exception):
    """Raised when a Stripe refund identifier is already persisted."""


class DuplicateRefundIdempotencyError(Exception):
    """Raised when a payment/idempotency pair is already persisted."""


class RefundConstraintError(Exception):
    """Raised when refund persistence violates a database constraint."""


def raise_write_error(exc: IntegrityError) -> None:
    constraint = getattr(getattr(exc.orig, "diag", None), "constraint_name", None)
    if constraint == "uq_refunds_provider_refund_id":
        raise DuplicateRefundReferenceError from exc
    if constraint == "uq_refunds_payment_id_idempotency_key_hash":
        raise DuplicateRefundIdempotencyError from exc
    raise RefundConstraintError from exc


class RefundRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    def get_payment_for_update(self, payment_id: int) -> Payment | None:
        return self._session.scalar(
            select(Payment).where(Payment.id == payment_id).with_for_update()
        )

    def get_by_idempotency_key_hash(
        self,
        payment_id: int,
        key_hash: str,
    ) -> Refund | None:
        return self._session.scalar(
            select(Refund).where(
                Refund.payment_id == payment_id,
                Refund.idempotency_key_hash == key_hash,
            )
        )

    def get_by_id_for_update(self, refund_id: int) -> Refund | None:
        return self._session.scalar(
            select(Refund).where(Refund.id == refund_id).with_for_update()
        )

    def active_amount(self, payment_id: int) -> Decimal:
        value = self._session.scalar(
            select(func.coalesce(func.sum(Refund.amount), 0)).where(
                Refund.payment_id == payment_id,
                Refund.status.in_(
                    [
                        RefundStatus.PENDING,
                        RefundStatus.REQUIRES_ACTION,
                        RefundStatus.SUCCEEDED,
                    ]
                ),
            )
        )
        return Decimal(value or 0)

    def succeeded_amount(self, payment_id: int) -> Decimal:
        self._session.flush()
        value = self._session.scalar(
            select(func.coalesce(func.sum(Refund.amount), 0)).where(
                Refund.payment_id == payment_id,
                Refund.status == RefundStatus.SUCCEEDED,
            )
        )
        return Decimal(value or 0)

    def create_reservation(
        self,
        *,
        payment_id: int,
        amount: Decimal,
        currency: str,
        reason: str | None,
        idempotency_key_hash: str,
        request_fingerprint: str,
    ) -> Refund:
        refund = Refund(
            payment_id=payment_id,
            provider="stripe",
            amount=amount,
            currency=currency,
            status=RefundStatus.PENDING,
            reason=reason,
            idempotency_key_hash=idempotency_key_hash,
            request_fingerprint=request_fingerprint,
        )
        self._session.add(refund)
        try:
            self._session.commit()
        except IntegrityError as exc:
            self._session.rollback()
            raise_write_error(exc)
        self._session.refresh(refund)
        return refund

    def release_reservation(self, refund: Refund) -> None:
        self._session.delete(refund)
        try:
            self._session.commit()
        except IntegrityError as exc:
            self._session.rollback()
            raise_write_error(exc)

    def commit_provider_result(
        self,
        *,
        refund: Refund,
        payment: Payment,
        provider_refund_id: str,
        status: RefundStatus,
        reason: str | None,
        failure_reason: str | None,
        provider_created_at: datetime,
        payment_status: PaymentStatus,
    ) -> Refund:
        refund.provider_refund_id = provider_refund_id
        refund.status = status
        refund.reason = reason
        refund.failure_reason = failure_reason
        refund.provider_created_at = provider_created_at
        payment.status = payment_status
        try:
            self._session.commit()
        except IntegrityError as exc:
            self._session.rollback()
            raise_write_error(exc)
        self._session.refresh(refund)
        return refund

    def get_by_id(self, refund_id: int) -> Refund | None:
        return self._session.get(Refund, refund_id)

    def payment_exists(self, payment_id: int) -> bool:
        return self._session.get(Payment, payment_id) is not None

    def list_page(
        self,
        *,
        payment_id: int,
        offset: int,
        limit: int,
    ) -> tuple[list[Refund], int]:
        total = (
            self._session.scalar(
                select(func.count())
                .select_from(Refund)
                .where(Refund.payment_id == payment_id)
            )
            or 0
        )
        refunds = self._session.scalars(
            select(Refund)
            .where(Refund.payment_id == payment_id)
            .order_by(Refund.id.asc())
            .offset(offset)
            .limit(limit)
        ).all()
        return list(refunds), total


def unix_timestamp(value: int) -> datetime:
    return datetime.fromtimestamp(value, tz=UTC)

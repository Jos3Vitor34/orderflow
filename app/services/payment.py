from app.models.order import OrderStatus
from app.models.payment import Payment, PaymentStatus
from app.repositories.payment import (
    DuplicatePaymentReferenceError,
    PaymentRepository,
)
from app.schemas.payment import (
    PaymentCreate,
    PaymentListResponse,
    PaymentResponse,
    PaymentUpdate,
)


class PaymentNotFoundError(Exception):
    """Raised when a payment identifier does not exist."""


class PaymentOrderNotFoundError(Exception):
    """Raised when a payment references a missing order."""


class CancelledOrderPaymentError(Exception):
    """Raised when a new payment is requested for a cancelled order."""


class NonPositiveOrderTotalError(Exception):
    """Raised when an order total cannot satisfy the payment constraint."""


class InvalidPaymentStatusTransitionError(Exception):
    """Raised when a payment status transition violates the lifecycle."""


ALLOWED_STATUS_TRANSITIONS: dict[PaymentStatus, frozenset[PaymentStatus]] = {
    PaymentStatus.PENDING: frozenset({PaymentStatus.APPROVED, PaymentStatus.FAILED}),
    PaymentStatus.APPROVED: frozenset(
        {PaymentStatus.PARTIALLY_REFUNDED, PaymentStatus.REFUNDED}
    ),
    PaymentStatus.FAILED: frozenset(),
    PaymentStatus.PARTIALLY_REFUNDED: frozenset({PaymentStatus.REFUNDED}),
    PaymentStatus.REFUNDED: frozenset(),
}


def validate_payment_status_transition(
    current_status: PaymentStatus,
    new_status: PaymentStatus,
) -> None:
    if new_status == current_status:
        return
    if new_status not in ALLOWED_STATUS_TRANSITIONS[current_status]:
        raise InvalidPaymentStatusTransitionError


class PaymentService:
    def __init__(self, repository: PaymentRepository) -> None:
        self._repository = repository

    @staticmethod
    def _same_idempotent_request(payment: Payment, data: PaymentCreate) -> bool:
        return payment.order_id == data.order_id and payment.provider == data.provider

    def create(self, data: PaymentCreate) -> Payment:
        if data.provider_reference is not None:
            existing = self._repository.get_by_provider_reference(
                data.provider_reference
            )
            if existing is not None:
                if self._same_idempotent_request(existing, data):
                    return existing
                raise DuplicatePaymentReferenceError

        order = self._repository.get_order_for_payment(data.order_id)
        if order is None:
            raise PaymentOrderNotFoundError
        if order.status == OrderStatus.CANCELLED:
            raise CancelledOrderPaymentError
        if order.total_amount <= 0:
            raise NonPositiveOrderTotalError

        try:
            return self._repository.create(
                order_id=order.id,
                provider=data.provider,
                provider_reference=data.provider_reference,
                amount=order.total_amount,
            )
        except DuplicatePaymentReferenceError:
            if data.provider_reference is not None:
                existing = self._repository.get_by_provider_reference(
                    data.provider_reference
                )
                if existing is not None and self._same_idempotent_request(
                    existing, data
                ):
                    return existing
            raise

    def list(self, *, page: int, page_size: int) -> PaymentListResponse:
        payments, total = self._repository.list_page(
            offset=(page - 1) * page_size,
            limit=page_size,
        )
        pages = (total + page_size - 1) // page_size
        return PaymentListResponse(
            items=[PaymentResponse.model_validate(payment) for payment in payments],
            total=total,
            page=page,
            page_size=page_size,
            pages=pages,
        )

    def get(self, payment_id: int) -> Payment:
        payment = self._repository.get_by_id(payment_id)
        if payment is None:
            raise PaymentNotFoundError
        return payment

    def update(self, payment_id: int, data: PaymentUpdate) -> Payment:
        payment = self._repository.get_by_id_for_update(payment_id)
        if payment is None:
            raise PaymentNotFoundError
        validate_payment_status_transition(payment.status, data.status)
        if data.status == payment.status:
            return payment
        return self._repository.update_status(payment, data.status)

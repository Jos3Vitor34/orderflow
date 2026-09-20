import json
from dataclasses import dataclass
from decimal import Decimal
from hashlib import sha256

from app.integrations.stripe import (
    StripeAuthenticationError,
    StripeConfigurationError,
    StripeIdempotencyError,
    StripeInvalidRequestError,
)
from app.integrations.stripe_refund import StripeRefund, StripeRefundGateway
from app.models.payment import Payment, PaymentStatus
from app.models.refund import Refund, RefundStatus
from app.repositories.refund import RefundRepository, unix_timestamp
from app.schemas.refund import RefundCreate, RefundListResponse, RefundResponse
from app.services.payment import validate_payment_status_transition
from app.services.stripe_payment import StripePaymentAmountError, decimal_to_minor_units

STRIPE_PROVIDER = "stripe"
REFUNDABLE_PAYMENT_STATUSES = frozenset(
    {PaymentStatus.APPROVED, PaymentStatus.PARTIALLY_REFUNDED}
)
REFUND_TRANSITIONS: dict[RefundStatus, frozenset[RefundStatus]] = {
    RefundStatus.PENDING: frozenset(
        {
            RefundStatus.REQUIRES_ACTION,
            RefundStatus.SUCCEEDED,
            RefundStatus.FAILED,
            RefundStatus.CANCELED,
        }
    ),
    RefundStatus.REQUIRES_ACTION: frozenset(
        {
            RefundStatus.PENDING,
            RefundStatus.SUCCEEDED,
            RefundStatus.FAILED,
            RefundStatus.CANCELED,
        }
    ),
    RefundStatus.SUCCEEDED: frozenset(),
    RefundStatus.FAILED: frozenset(),
    RefundStatus.CANCELED: frozenset(),
}


class RefundNotFoundError(Exception):
    """Raised when a refund does not exist."""


class RefundPaymentNotFoundError(Exception):
    """Raised when a requested payment does not exist."""


class RefundPaymentNotEligibleError(Exception):
    """Raised when a payment is not in a refundable state."""


class RefundPaymentProviderError(Exception):
    """Raised when a payment is not backed by a Stripe PaymentIntent."""


class RefundAmountError(Exception):
    """Raised when a refund amount is invalid or exceeds the available balance."""


class RefundFullyRefundedError(Exception):
    """Raised when a payment has no refundable balance."""


class RefundIdempotencyConflictError(Exception):
    """Raised when an idempotency key is reused with a different payload."""


class InvalidRefundStatusTransitionError(Exception):
    """Raised when a Refund status would regress or leave a terminal state."""


class StripeRefundMismatchError(Exception):
    """Raised when Stripe returns a refund inconsistent with the reservation."""


@dataclass(frozen=True)
class RefundReceipt:
    refund: Refund
    created: bool


def validate_refund_status_transition(
    current_status: RefundStatus,
    new_status: RefundStatus,
) -> None:
    if current_status == new_status:
        return
    if new_status not in REFUND_TRANSITIONS[current_status]:
        raise InvalidRefundStatusTransitionError


def payment_status_for_refunded_amount(
    payment: Payment,
    succeeded_amount: Decimal,
) -> PaymentStatus:
    if succeeded_amount < 0 or succeeded_amount > payment.amount:
        raise RefundAmountError
    if succeeded_amount == payment.amount:
        return PaymentStatus.REFUNDED
    if succeeded_amount > 0:
        return PaymentStatus.PARTIALLY_REFUNDED
    return PaymentStatus.APPROVED


def refund_request_fingerprint(data: RefundCreate) -> str:
    canonical = json.dumps(
        {
            "amount": str(data.amount) if data.amount is not None else None,
            "reason": data.reason.value if data.reason is not None else None,
        },
        sort_keys=True,
        separators=(",", ":"),
    )
    return sha256(canonical.encode()).hexdigest()


def refund_idempotency_hash(operation_key: str) -> str:
    return sha256(operation_key.encode()).hexdigest()


def build_stripe_refund_idempotency_key(payment_id: int, key_hash: str) -> str:
    return f"orderflow:stripe-refund:v1:{payment_id}:{key_hash}"


class RefundService:
    def __init__(
        self,
        repository: RefundRepository,
        gateway: StripeRefundGateway,
        *,
        currency: str,
    ) -> None:
        self._repository = repository
        self._gateway = gateway
        self._currency = currency

    @staticmethod
    def _validate_payment(payment: Payment) -> str:
        if payment.provider != STRIPE_PROVIDER or not payment.provider_reference:
            raise RefundPaymentProviderError
        if payment.status not in REFUNDABLE_PAYMENT_STATUSES:
            if payment.status == PaymentStatus.REFUNDED:
                raise RefundFullyRefundedError
            raise RefundPaymentNotEligibleError
        return payment.provider_reference

    def _reserve(
        self,
        payment_id: int,
        data: RefundCreate,
        *,
        operation_key: str,
    ) -> tuple[Refund, str, bool]:
        key_hash = refund_idempotency_hash(operation_key)
        fingerprint = refund_request_fingerprint(data)
        payment = self._repository.get_payment_for_update(payment_id)
        if payment is None:
            raise RefundPaymentNotFoundError

        existing = self._repository.get_by_idempotency_key_hash(payment_id, key_hash)
        if existing is not None:
            if existing.request_fingerprint != fingerprint:
                raise RefundIdempotencyConflictError
            if payment.provider != STRIPE_PROVIDER or not payment.provider_reference:
                raise RefundPaymentProviderError
            payment_intent = payment.provider_reference
            return existing, payment_intent, False

        payment_intent = self._validate_payment(payment)
        available = payment.amount - self._repository.active_amount(payment.id)
        if available <= 0:
            raise RefundFullyRefundedError
        amount = available if data.amount is None else data.amount
        try:
            decimal_to_minor_units(amount)
        except StripePaymentAmountError as exc:
            raise RefundAmountError from exc
        if amount > available:
            raise RefundAmountError

        refund = self._repository.create_reservation(
            payment_id=payment.id,
            amount=amount,
            currency=self._currency,
            reason=data.reason.value if data.reason is not None else None,
            idempotency_key_hash=key_hash,
            request_fingerprint=fingerprint,
        )
        return refund, payment_intent, True

    def _release_definitive_failure(self, refund_id: int) -> None:
        refund = self._repository.get_by_id_for_update(refund_id)
        if refund is not None and refund.provider_refund_id is None:
            self._repository.release_reservation(refund)

    def _finish(
        self,
        *,
        refund_id: int,
        payment_id: int,
        payment_intent: str,
        provider_refund: StripeRefund,
    ) -> Refund:
        payment = self._repository.get_payment_for_update(payment_id)
        refund = self._repository.get_by_id_for_update(refund_id)
        if payment is None or refund is None:
            raise StripeRefundMismatchError
        if (
            provider_refund.payment_intent != payment_intent
            or provider_refund.amount != decimal_to_minor_units(refund.amount)
            or provider_refund.currency != self._currency
            or (
                refund.provider_refund_id is not None
                and refund.provider_refund_id != provider_refund.id
            )
        ):
            raise StripeRefundMismatchError
        try:
            target_status = RefundStatus(provider_refund.status)
        except ValueError as exc:
            raise StripeRefundMismatchError from exc
        validate_refund_status_transition(refund.status, target_status)
        refund.status = target_status
        succeeded_amount = self._repository.succeeded_amount(payment.id)
        payment_status = payment_status_for_refunded_amount(payment, succeeded_amount)
        validate_payment_status_transition(payment.status, payment_status)
        return self._repository.commit_provider_result(
            refund=refund,
            payment=payment,
            provider_refund_id=provider_refund.id,
            status=target_status,
            reason=provider_refund.reason,
            failure_reason=provider_refund.failure_reason,
            provider_created_at=unix_timestamp(provider_refund.created),
            payment_status=payment_status,
        )

    def create(
        self,
        payment_id: int,
        data: RefundCreate,
        *,
        operation_key: str,
    ) -> RefundReceipt:
        refund, payment_intent, created = self._reserve(
            payment_id,
            data,
            operation_key=operation_key,
        )
        if refund.provider_refund_id is not None:
            return RefundReceipt(refund=refund, created=False)

        try:
            provider_refund = self._gateway.create_refund(
                payment_intent=payment_intent,
                amount=decimal_to_minor_units(refund.amount),
                reason=refund.reason,
                metadata={
                    "orderflow_refund_id": str(refund.id),
                    "orderflow_payment_id": str(payment_id),
                },
                idempotency_key=build_stripe_refund_idempotency_key(
                    payment_id,
                    refund.idempotency_key_hash or "",
                ),
            )
        except (
            StripeConfigurationError,
            StripeAuthenticationError,
            StripeInvalidRequestError,
            StripeIdempotencyError,
        ):
            self._release_definitive_failure(refund.id)
            raise

        finalized = self._finish(
            refund_id=refund.id,
            payment_id=payment_id,
            payment_intent=payment_intent,
            provider_refund=provider_refund,
        )
        return RefundReceipt(refund=finalized, created=created)

    def get(self, refund_id: int) -> Refund:
        refund = self._repository.get_by_id(refund_id)
        if refund is None:
            raise RefundNotFoundError
        return refund

    def list(
        self,
        payment_id: int,
        *,
        page: int,
        page_size: int,
    ) -> RefundListResponse:
        if not self._repository.payment_exists(payment_id):
            raise RefundPaymentNotFoundError
        refunds, total = self._repository.list_page(
            payment_id=payment_id,
            offset=(page - 1) * page_size,
            limit=page_size,
        )
        return RefundListResponse(
            items=[RefundResponse.model_validate(refund) for refund in refunds],
            total=total,
            page=page,
            page_size=page_size,
            pages=(total + page_size - 1) // page_size,
        )

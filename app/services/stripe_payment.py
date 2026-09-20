from dataclasses import dataclass
from decimal import Decimal
from hashlib import sha256

from app.integrations.stripe import StripeGateway, StripePaymentIntent
from app.models.order import OrderStatus
from app.models.payment import Payment
from app.repositories.payment import DuplicatePaymentReferenceError
from app.repositories.stripe_payment import StripePaymentRepository
from app.schemas.stripe_payment import StripePaymentCreate

STRIPE_PROVIDER = "stripe"
BRL_MINOR_UNIT_FACTOR = Decimal("100")
STRIPE_BRL_MINIMUM_MINOR = 50
STRIPE_BRL_MAXIMUM_MINOR = 99_999_999


class StripePaymentOrderNotFoundError(Exception):
    """Raised when a Stripe payment references a missing order."""


class StripePaymentCancelledOrderError(Exception):
    """Raised when a cancelled order is sent to Stripe."""


class StripePaymentAmountError(Exception):
    """Raised when an amount cannot be represented or charged in BRL."""


class StripePaymentOrderChangedError(Exception):
    """Raised when the order changes across the external API boundary."""


class StripePaymentIntentMismatchError(Exception):
    """Raised when Stripe returns data inconsistent with the request."""


@dataclass(frozen=True)
class StripePaymentReceipt:
    payment: Payment
    payment_intent: StripePaymentIntent
    created: bool


def decimal_to_minor_units(amount: Decimal) -> int:
    quantum = Decimal("0.01")
    quantized = amount.quantize(quantum)
    if amount != quantized or amount <= 0:
        raise StripePaymentAmountError
    minor_units = quantized * BRL_MINOR_UNIT_FACTOR
    return int(minor_units)


def build_stripe_idempotency_key(order_id: int, operation_key: str) -> str:
    digest = sha256(operation_key.encode("utf-8")).hexdigest()
    return f"orderflow:stripe-payment:v1:{order_id}:{digest}"


class StripePaymentService:
    def __init__(
        self,
        repository: StripePaymentRepository,
        gateway: StripeGateway,
        *,
        currency: str,
    ) -> None:
        self._repository = repository
        self._gateway = gateway
        self._currency = currency

    @staticmethod
    def _validate_existing(
        payment: Payment,
        *,
        order_id: int,
        amount: Decimal,
    ) -> Payment:
        if (
            payment.provider != STRIPE_PROVIDER
            or payment.order_id != order_id
            or payment.amount != amount
        ):
            raise DuplicatePaymentReferenceError
        return payment

    @staticmethod
    def _validate_minor_amount(minor_amount: int) -> None:
        if not STRIPE_BRL_MINIMUM_MINOR <= minor_amount <= STRIPE_BRL_MAXIMUM_MINOR:
            raise StripePaymentAmountError

    def create(
        self,
        data: StripePaymentCreate,
        *,
        operation_key: str,
    ) -> StripePaymentReceipt:
        snapshot = self._repository.get_order_snapshot(data.order_id)
        if snapshot is None:
            raise StripePaymentOrderNotFoundError
        if snapshot.status == OrderStatus.CANCELLED:
            raise StripePaymentCancelledOrderError

        minor_amount = decimal_to_minor_units(snapshot.total_amount)
        self._validate_minor_amount(minor_amount)
        idempotency_key = build_stripe_idempotency_key(
            snapshot.id,
            operation_key,
        )
        payment_intent = self._gateway.create_payment_intent(
            amount=minor_amount,
            currency=self._currency,
            metadata={"orderflow_order_id": str(snapshot.id)},
            idempotency_key=idempotency_key,
        )
        if (
            payment_intent.amount != minor_amount
            or payment_intent.currency != self._currency
        ):
            raise StripePaymentIntentMismatchError

        existing = self._repository.get_by_provider_reference(payment_intent.id)
        if existing is not None:
            return StripePaymentReceipt(
                payment=self._validate_existing(
                    existing,
                    order_id=snapshot.id,
                    amount=snapshot.total_amount,
                ),
                payment_intent=payment_intent,
                created=False,
            )

        order = self._repository.get_order_for_update(snapshot.id)
        if order is None:
            raise StripePaymentOrderNotFoundError
        if order.status == OrderStatus.CANCELLED:
            raise StripePaymentCancelledOrderError
        if order.total_amount != snapshot.total_amount:
            raise StripePaymentOrderChangedError

        existing = self._repository.get_by_provider_reference(payment_intent.id)
        if existing is not None:
            return StripePaymentReceipt(
                payment=self._validate_existing(
                    existing,
                    order_id=order.id,
                    amount=order.total_amount,
                ),
                payment_intent=payment_intent,
                created=False,
            )

        try:
            payment = self._repository.create(
                order_id=order.id,
                provider_reference=payment_intent.id,
                amount=order.total_amount,
            )
        except DuplicatePaymentReferenceError:
            existing = self._repository.get_by_provider_reference(payment_intent.id)
            if existing is None:
                raise
            payment = self._validate_existing(
                existing,
                order_id=order.id,
                amount=order.total_amount,
            )
            return StripePaymentReceipt(
                payment=payment,
                payment_intent=payment_intent,
                created=False,
            )

        return StripePaymentReceipt(
            payment=payment,
            payment_intent=payment_intent,
            created=True,
        )

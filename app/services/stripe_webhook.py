from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

from app.integrations.stripe_webhook import VerifiedStripeEvent
from app.models.payment import PaymentStatus
from app.models.refund import RefundStatus
from app.models.webhook_event import WebhookEvent
from app.repositories.stripe_webhook import StripeWebhookRepository
from app.repositories.webhook_event import (
    DuplicateWebhookEventIdError,
)
from app.services.payment import (
    InvalidPaymentStatusTransitionError,
    validate_payment_status_transition,
)
from app.services.refund import (
    InvalidRefundStatusTransitionError,
    RefundAmountError,
    payment_status_for_refunded_amount,
    validate_refund_status_transition,
)
from app.services.stripe_payment import (
    STRIPE_PROVIDER,
    StripePaymentAmountError,
    decimal_to_minor_units,
)

STRIPE_PAYMENT_EVENTS: dict[str, PaymentStatus] = {
    "payment_intent.succeeded": PaymentStatus.APPROVED,
    "payment_intent.payment_failed": PaymentStatus.FAILED,
}
STRIPE_REFUND_EVENTS = frozenset({"refund.created", "refund.updated", "refund.failed"})


@dataclass(frozen=True)
class StripeWebhookReceipt:
    event: WebhookEvent
    created: bool
    processed: bool


class StripeWebhookService:
    def __init__(
        self,
        repository: StripeWebhookRepository,
        *,
        currency: str,
    ) -> None:
        self._repository = repository
        self._currency = currency

    @staticmethod
    def _existing_receipt(event: WebhookEvent) -> StripeWebhookReceipt:
        return StripeWebhookReceipt(
            event=event,
            created=False,
            processed=event.processed_at is not None,
        )

    @staticmethod
    def _payment_intent(payload: dict[str, Any]) -> tuple[str, int, str] | None:
        data = payload.get("data")
        if not isinstance(data, dict):
            return None
        payment_intent = data.get("object")
        if not isinstance(payment_intent, dict):
            return None
        intent_id = payment_intent.get("id")
        amount = payment_intent.get("amount")
        currency = payment_intent.get("currency")
        if (
            not isinstance(intent_id, str)
            or not intent_id
            or len(intent_id) > 255
            or not isinstance(amount, int)
            or isinstance(amount, bool)
            or not isinstance(currency, str)
            or not currency
        ):
            return None
        return intent_id, amount, currency

    @staticmethod
    def _refund_data(
        event: VerifiedStripeEvent,
    ) -> (
        tuple[
            str,
            str,
            int,
            str,
            RefundStatus,
            str | None,
            str | None,
            datetime,
            datetime,
            int | None,
        ]
        | None
    ):
        data = event.payload.get("data")
        refund = data.get("object") if isinstance(data, dict) else None
        if not isinstance(refund, dict):
            return None
        refund_id = refund.get("id")
        payment_intent = refund.get("payment_intent")
        amount = refund.get("amount")
        currency = refund.get("currency")
        status_value = refund.get("status")
        reason = refund.get("reason")
        failure_reason = refund.get("failure_reason")
        created = refund.get("created")
        event_created = event.payload.get("created")
        metadata = refund.get("metadata")
        local_refund_id: int | None = None
        if isinstance(metadata, dict):
            local_id = metadata.get("orderflow_refund_id")
            if isinstance(local_id, str) and local_id.isdecimal():
                local_refund_id = int(local_id)
        try:
            refund_status = (
                RefundStatus(status_value) if isinstance(status_value, str) else None
            )
        except (TypeError, ValueError):
            return None
        if refund_status is None:
            return None
        if (
            not isinstance(refund_id, str)
            or not refund_id
            or len(refund_id) > 255
            or not isinstance(payment_intent, str)
            or not payment_intent
            or len(payment_intent) > 255
            or not isinstance(amount, int)
            or isinstance(amount, bool)
            or amount <= 0
            or not isinstance(currency, str)
            or not currency
            or (reason is not None and not isinstance(reason, str))
            or (failure_reason is not None and not isinstance(failure_reason, str))
            or not isinstance(created, int)
            or not isinstance(event_created, int)
        ):
            return None
        return (
            refund_id,
            payment_intent,
            amount,
            currency,
            refund_status,
            reason,
            failure_reason,
            datetime.fromtimestamp(created, tz=UTC),
            datetime.fromtimestamp(event_created, tz=UTC),
            local_refund_id,
        )

    def _receive_refund(
        self,
        webhook_event: WebhookEvent,
        event: VerifiedStripeEvent,
    ) -> StripeWebhookReceipt:
        refund_data = self._refund_data(event)
        if refund_data is None:
            return StripeWebhookReceipt(
                event=self._repository.commit_received(webhook_event),
                created=True,
                processed=False,
            )
        (
            provider_refund_id,
            payment_intent,
            minor_amount,
            currency,
            target_status,
            reason,
            failure_reason,
            provider_created_at,
            event_created_at,
            local_refund_id,
        ) = refund_data

        payment = self._repository.get_payment_for_update(payment_intent)
        if payment is None or payment.provider != STRIPE_PROVIDER:
            return StripeWebhookReceipt(
                event=self._repository.commit_received(webhook_event),
                created=True,
                processed=False,
            )
        amount = Decimal(minor_amount) / Decimal("100")
        if currency != self._currency or amount > payment.amount:
            return StripeWebhookReceipt(
                event=self._repository.commit_received(webhook_event),
                created=True,
                processed=False,
            )

        refund = self._repository.get_refund_by_provider_id_for_update(
            provider_refund_id
        )
        if refund is None and local_refund_id is not None:
            refund = self._repository.get_refund_by_id_for_update(local_refund_id)
        if refund is not None:
            if (
                refund.payment_id != payment.id
                or refund.provider != STRIPE_PROVIDER
                or refund.amount != amount
                or refund.currency != currency
                or (
                    refund.provider_refund_id is not None
                    and refund.provider_refund_id != provider_refund_id
                )
                or (
                    refund.last_provider_event_created_at is not None
                    and event_created_at < refund.last_provider_event_created_at
                )
            ):
                return StripeWebhookReceipt(
                    event=self._repository.commit_received(webhook_event),
                    created=True,
                    processed=False,
                )
            try:
                validate_refund_status_transition(refund.status, target_status)
            except InvalidRefundStatusTransitionError:
                return StripeWebhookReceipt(
                    event=self._repository.commit_received(webhook_event),
                    created=True,
                    processed=False,
                )

        succeeded_amount = self._repository.succeeded_refund_amount(payment.id)
        if target_status == RefundStatus.SUCCEEDED and (
            refund is None or refund.status != RefundStatus.SUCCEEDED
        ):
            succeeded_amount += amount
        try:
            payment_status = payment_status_for_refunded_amount(
                payment,
                succeeded_amount,
            )
            validate_payment_status_transition(payment.status, payment_status)
        except (RefundAmountError, InvalidPaymentStatusTransitionError):
            return StripeWebhookReceipt(
                event=self._repository.commit_received(webhook_event),
                created=True,
                processed=False,
            )

        if refund is None:
            refund = self._repository.create_external_refund(
                payment_id=payment.id,
                provider_refund_id=provider_refund_id,
                amount=amount,
                currency=currency,
                status=target_status,
                reason=reason,
                failure_reason=failure_reason,
                provider_created_at=provider_created_at,
                event_created_at=event_created_at,
            )
        return StripeWebhookReceipt(
            event=self._repository.commit_refund_processed(
                webhook_event=webhook_event,
                refund=refund,
                payment=payment,
                refund_status=target_status,
                payment_status=payment_status,
                provider_refund_id=provider_refund_id,
                reason=reason,
                failure_reason=failure_reason,
                provider_created_at=provider_created_at,
                event_created_at=event_created_at,
            ),
            created=True,
            processed=True,
        )

    def receive(self, event: VerifiedStripeEvent) -> StripeWebhookReceipt:
        existing = self._repository.get_by_provider_event_id(event.id)
        if existing is not None:
            return self._existing_receipt(existing)

        try:
            webhook_event = self._repository.create_received(
                provider=STRIPE_PROVIDER,
                provider_event_id=event.id,
                event_type=event.type,
                payload=event.payload,
            )
        except DuplicateWebhookEventIdError:
            existing = self._repository.get_by_provider_event_id(event.id)
            if existing is None:
                raise
            return self._existing_receipt(existing)

        target_status = STRIPE_PAYMENT_EVENTS.get(event.type)
        if event.type in STRIPE_REFUND_EVENTS:
            return self._receive_refund(webhook_event, event)
        if target_status is None:
            return StripeWebhookReceipt(
                event=self._repository.commit_received(webhook_event),
                created=True,
                processed=False,
            )

        payment_intent = self._payment_intent(event.payload)
        if payment_intent is None:
            return StripeWebhookReceipt(
                event=self._repository.commit_received(webhook_event),
                created=True,
                processed=False,
            )
        intent_id, intent_amount, intent_currency = payment_intent

        payment = self._repository.get_payment_for_update(intent_id)
        if payment is None or payment.provider != STRIPE_PROVIDER:
            return StripeWebhookReceipt(
                event=self._repository.commit_received(webhook_event),
                created=True,
                processed=False,
            )

        try:
            local_amount = decimal_to_minor_units(payment.amount)
        except StripePaymentAmountError:
            local_amount = -1
        if intent_amount != local_amount or intent_currency != self._currency:
            return StripeWebhookReceipt(
                event=self._repository.commit_received(webhook_event),
                created=True,
                processed=False,
            )

        try:
            validate_payment_status_transition(payment.status, target_status)
        except InvalidPaymentStatusTransitionError:
            return StripeWebhookReceipt(
                event=self._repository.commit_received(webhook_event),
                created=True,
                processed=False,
            )

        return StripeWebhookReceipt(
            event=self._repository.commit_processed(
                webhook_event,
                payment,
                target_status,
            ),
            created=True,
            processed=True,
        )

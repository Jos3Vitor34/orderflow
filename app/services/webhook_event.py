from dataclasses import dataclass

from app.models.payment import PaymentStatus
from app.models.webhook_event import WebhookEvent
from app.repositories.webhook_event import (
    DuplicateWebhookEventIdError,
    WebhookEventRepository,
)
from app.schemas.webhook_event import (
    WebhookEventCreate,
    WebhookEventListResponse,
    WebhookEventResponse,
)
from app.services.payment import (
    InvalidPaymentStatusTransitionError,
    validate_payment_status_transition,
)

RECOGNIZED_PAYMENT_EVENTS: dict[str, PaymentStatus] = {
    "payment.approved": PaymentStatus.APPROVED,
    "payment.failed": PaymentStatus.FAILED,
    "payment.refunded": PaymentStatus.REFUNDED,
}


class WebhookEventNotFoundError(Exception):
    """Raised when a webhook event identifier does not exist."""


class WebhookEventCollisionError(Exception):
    """Raised when an event ID is reused with different structural content."""


class InvalidWebhookPayloadError(Exception):
    """Raised when a recognized event lacks its required internal reference."""


class WebhookPaymentNotFoundError(Exception):
    """Raised when a recognized event references no existing payment."""


class WebhookPaymentProviderMismatchError(Exception):
    """Raised when the endpoint provider does not own the referenced payment."""


@dataclass(frozen=True)
class WebhookReceipt:
    event: WebhookEvent
    created: bool


class WebhookEventService:
    def __init__(self, repository: WebhookEventRepository) -> None:
        self._repository = repository

    @staticmethod
    def _same_event(
        webhook_event: WebhookEvent,
        *,
        provider: str,
        data: WebhookEventCreate,
    ) -> bool:
        return (
            webhook_event.provider == provider
            and webhook_event.event_type == data.event_type
            and webhook_event.payload == data.payload
        )

    def _existing_receipt(
        self,
        webhook_event: WebhookEvent,
        *,
        provider: str,
        data: WebhookEventCreate,
    ) -> WebhookReceipt:
        if not self._same_event(webhook_event, provider=provider, data=data):
            raise WebhookEventCollisionError
        return WebhookReceipt(event=webhook_event, created=False)

    @staticmethod
    def _payment_reference(
        target_status: PaymentStatus | None,
        data: WebhookEventCreate,
    ) -> str | None:
        if target_status is None:
            return None
        provider_reference = data.payload.get("provider_reference")
        if (
            not isinstance(provider_reference, str)
            or not provider_reference
            or len(provider_reference) > 255
        ):
            raise InvalidWebhookPayloadError
        return provider_reference

    def receive(self, *, provider: str, data: WebhookEventCreate) -> WebhookReceipt:
        existing = self._repository.get_by_provider_event_id(data.provider_event_id)
        if existing is not None:
            return self._existing_receipt(existing, provider=provider, data=data)

        target_status = RECOGNIZED_PAYMENT_EVENTS.get(data.event_type)
        provider_reference = self._payment_reference(target_status, data)

        try:
            webhook_event = self._repository.create_received(
                provider=provider,
                provider_event_id=data.provider_event_id,
                event_type=data.event_type,
                payload=dict(data.payload),
            )
        except DuplicateWebhookEventIdError:
            existing = self._repository.get_by_provider_event_id(data.provider_event_id)
            if existing is None:
                raise
            return self._existing_receipt(existing, provider=provider, data=data)

        if target_status is None:
            return WebhookReceipt(
                event=self._repository.commit_received(webhook_event),
                created=True,
            )

        assert provider_reference is not None
        payment = self._repository.get_payment_for_update(provider_reference)
        if payment is None:
            self._repository.commit_received(webhook_event)
            raise WebhookPaymentNotFoundError
        if payment.provider != provider:
            self._repository.commit_received(webhook_event)
            raise WebhookPaymentProviderMismatchError

        try:
            validate_payment_status_transition(payment.status, target_status)
        except InvalidPaymentStatusTransitionError:
            self._repository.commit_received(webhook_event)
            raise

        return WebhookReceipt(
            event=self._repository.commit_processed(
                webhook_event,
                payment,
                target_status,
            ),
            created=True,
        )

    def list(self, *, page: int, page_size: int) -> WebhookEventListResponse:
        webhook_events, total = self._repository.list_page(
            offset=(page - 1) * page_size,
            limit=page_size,
        )
        pages = (total + page_size - 1) // page_size
        return WebhookEventListResponse(
            items=[
                WebhookEventResponse.model_validate(webhook_event)
                for webhook_event in webhook_events
            ],
            total=total,
            page=page,
            page_size=page_size,
            pages=pages,
        )

    def get(self, webhook_event_id: int) -> WebhookEvent:
        webhook_event = self._repository.get_by_id(webhook_event_id)
        if webhook_event is None:
            raise WebhookEventNotFoundError
        return webhook_event

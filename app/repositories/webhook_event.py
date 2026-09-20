from datetime import UTC, datetime

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.models.payment import Payment, PaymentStatus
from app.models.webhook_event import WebhookEvent


class DuplicateWebhookEventIdError(Exception):
    """Raised when a provider event identifier already exists."""


class WebhookEventConstraintError(Exception):
    """Raised when webhook persistence violates a database constraint."""


def raise_write_error(exc: IntegrityError) -> None:
    diagnostic = getattr(exc.orig, "diag", None)
    if getattr(diagnostic, "constraint_name", None) == (
        "webhook_events_provider_event_id_key"
    ):
        raise DuplicateWebhookEventIdError from exc
    raise WebhookEventConstraintError from exc


class WebhookEventRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    def get_by_provider_event_id(self, provider_event_id: str) -> WebhookEvent | None:
        statement = select(WebhookEvent).where(
            WebhookEvent.provider_event_id == provider_event_id
        )
        return self._session.scalar(statement)

    def create_received(
        self,
        *,
        provider: str,
        provider_event_id: str,
        event_type: str,
        payload: dict[str, object],
    ) -> WebhookEvent:
        webhook_event = WebhookEvent(
            provider=provider,
            provider_event_id=provider_event_id,
            event_type=event_type,
            payload=payload,
            processed_at=None,
        )
        self._session.add(webhook_event)
        try:
            self._session.flush()
        except IntegrityError as exc:
            self._session.rollback()
            raise_write_error(exc)
        return webhook_event

    def get_payment_for_update(self, provider_reference: str) -> Payment | None:
        statement = (
            select(Payment)
            .where(Payment.provider_reference == provider_reference)
            .with_for_update()
        )
        return self._session.scalar(statement)

    def commit_received(self, webhook_event: WebhookEvent) -> WebhookEvent:
        try:
            self._session.commit()
        except IntegrityError as exc:
            self._session.rollback()
            raise_write_error(exc)
        self._session.refresh(webhook_event)
        return webhook_event

    def commit_processed(
        self,
        webhook_event: WebhookEvent,
        payment: Payment,
        new_status: PaymentStatus,
    ) -> WebhookEvent:
        payment.status = new_status
        webhook_event.processed_at = datetime.now(UTC)
        try:
            self._session.commit()
        except IntegrityError as exc:
            self._session.rollback()
            raise_write_error(exc)
        self._session.refresh(webhook_event)
        return webhook_event

    def get_by_id(self, webhook_event_id: int) -> WebhookEvent | None:
        return self._session.get(WebhookEvent, webhook_event_id)

    def list_page(self, *, offset: int, limit: int) -> tuple[list[WebhookEvent], int]:
        total = (
            self._session.scalar(select(func.count()).select_from(WebhookEvent)) or 0
        )
        statement = (
            select(WebhookEvent)
            .order_by(WebhookEvent.id.asc())
            .offset(offset)
            .limit(limit)
        )
        return list(self._session.scalars(statement).all()), total
